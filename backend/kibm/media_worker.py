from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SOCKET_PATH = Path("/run/kibm-media/worker.sock")
VOLUME_PATH = Path("/run/kibm-media/volume")
SOURCE_DEVICE = "/dev/mmcblk0"
PISHRINK_PATH = Path("/opt/klipper-image-backup-manager/tools/pishrink.sh")
BLOCKGETSIZE64 = 0x80081272
CHUNK_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 4096
PRINTER_RECHECK_SECONDS = 10


@dataclass
class MediaJob:
    job_id: str
    state: str
    phase: str
    message: str
    image_size: int
    source_device: str
    shrink: bool
    keep_full: bool
    created_at: float = field(default_factory=time.time)
    sent_bytes: int = 0
    sha256: Optional[str] = None
    error: Optional[str] = None
    files: list[dict[str, Any]] = field(default_factory=list)
    active_downloads: int = 0


_lock = threading.RLock()
_job: Optional[MediaJob] = None
_worker_uid: Optional[int] = None


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def _disk_size(path: str) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if not stat.S_ISBLK(os.fstat(fd).st_mode):
            raise ValueError("Das gewählte Ziel ist kein Blockgerät.")
        value = bytearray(8)
        import fcntl

        fcntl.ioctl(fd, BLOCKGETSIZE64, value, True)
        return struct.unpack("Q", value)[0]
    finally:
        os.close(fd)


def _devices() -> list[dict[str, Any]]:
    flat = _all_devices()
    root_device = next((item for item in flat if "/" in item["mountpoints"]), None)
    while root_device and root_device["type"] != "disk":
        root_device = next((item for item in flat if item["path"] == root_device["parent"]), None)
    system_path = root_device["path"] if root_device else SOURCE_DEVICE
    return [
        item for item in flat
        if item["type"] == "disk"
        and item["path"] != system_path
        and item["path"] != SOURCE_DEVICE
        and item["transport"] == "usb"
        and not item["read_only"]
    ]


def _all_devices() -> list[dict[str, Any]]:
    result = _run([
        "lsblk", "--json", "--bytes", "--output",
        "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,RM,RO,TRAN,MAJ:MIN",
    ])
    flat: list[dict[str, Any]] = []

    def visit(items: list[dict[str, Any]], parent: Optional[str] = None) -> None:
        for item in items:
            path = str(item.get("path") or f"/dev/{item.get('name', '')}")
            flat.append({
                "name": str(item.get("name", "")),
                "path": path,
                "parent": parent,
                "type": item.get("type"),
                "size_bytes": item.get("size"),
                "filesystem": item.get("fstype"),
                "mountpoints": item.get("mountpoints") or ([item["mountpoint"]] if item.get("mountpoint") else []),
                "model": item.get("model"),
                "removable": bool(item.get("rm", False)),
                "read_only": bool(item.get("ro", False)),
                "transport": item.get("tran"),
                "major_minor": item.get("maj:min"),
            })
            visit(item.get("children") or [], path)

    visit(json.loads(result.stdout).get("blockdevices", []))
    return flat


def _selected_device(path: str) -> dict[str, Any]:
    device = next((item for item in _devices() if item["path"] == path), None)
    if not device:
        raise ValueError("Der ausgewählte USB-Stick wurde nicht mehr erkannt.")
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError("Das ausgewählte Ziel ist kein Blockgerät.")
    return device


def _printer_safe() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:7125/printer/info", timeout=2) as response:
            info = json.loads(response.read(1024 * 1024)).get("result", {})
        with urllib.request.urlopen(
            "http://127.0.0.1:7125/printer/objects/query?print_stats", timeout=2
        ) as response:
            stats = json.loads(response.read(1024 * 1024)).get("result", {}).get("status", {}).get("print_stats")
        return bool(
            isinstance(stats, dict)
            and stats.get("state", "").lower() in {"standby", "complete", "cancelled", "idle"}
            and (info.get("klippy_state") or info.get("state")) == "ready"
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return False


def _partition_path(disk: str) -> str:
    name = Path(disk).name
    return f"{disk}p1" if name[-1:].isdigit() else f"{disk}1"


def _unmount_children(device: dict[str, Any]) -> None:
    children = [item for item in _all_devices() if item["parent"] == device["path"]]
    for child in children:
        for mountpoint in child["mountpoints"]:
            if mountpoint in {"/", "/boot", "/boot/firmware"}:
                raise ValueError("Aus Sicherheitsgründen wird ein Systemlaufwerk nicht formatiert.")
            _run(["umount", "--", mountpoint])


def _format_usb(device: dict[str, Any]) -> None:
    current = _selected_device(device["path"])
    if current["major_minor"] != device["major_minor"] or current["size_bytes"] != device["size_bytes"]:
        raise ValueError("Der USB-Stick wurde vor dem Formatieren gewechselt. Es wurde nichts formatiert.")
    if os.path.ismount(str(VOLUME_PATH)):
        _run(["umount", "--", str(VOLUME_PATH)])
    _unmount_children(device)
    _run(["wipefs", "--all", "--force", "--", device["path"]])
    _run(["parted", "--script", "--", device["path"], "mklabel", "msdos", "mkpart", "primary", "1MiB", "100%"])
    _run(["partprobe", device["path"]])
    _run(["udevadm", "settle", "--timeout=10"])
    partition = _partition_path(device["path"])
    deadline = time.monotonic() + 10
    while not os.path.exists(partition) and time.monotonic() < deadline:
        time.sleep(0.25)
    if not os.path.exists(partition):
        raise OSError("Die neue USB-Partition wurde nach dem Formatieren nicht erkannt.")
    _run(["mkfs.exfat", "-n", "KIBM", partition], timeout=120)
    VOLUME_PATH.mkdir(parents=True, exist_ok=True)
    _run(["mount", "-t", "exfat", "-o", "rw", partition, str(VOLUME_PATH)])


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as image:
        while True:
            chunk = image.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _set_job(**values: Any) -> None:
    with _lock:
        if _job:
            for key, value in values.items():
                setattr(_job, key, value)


def _run_job(job: MediaJob, device: dict[str, Any], hostname: str) -> None:
    full_path: Optional[Path] = None
    try:
        if not _printer_safe():
            raise ValueError("Der Drucker ist nicht sicher im Standby. Es wurde noch nichts formatiert.")
        current_device = _selected_device(device["path"])
        if current_device["major_minor"] != device["major_minor"] or current_device["size_bytes"] != device["size_bytes"]:
            raise ValueError("Der USB-Stick wurde seit der Auswahl gewechselt. Es wurde nichts formatiert.")
        raw_bytes = _disk_size(SOURCE_DEVICE)
        # Shrinking is done into a second file first. The complete image remains
        # intact until the smaller copy has passed validation.
        required_bytes = raw_bytes * (2 if job.shrink else 1)
        required_bytes += max(2 * 1024**3, raw_bytes // 10)
        if int(device["size_bytes"]) < required_bytes:
            raise ValueError(
                "Der USB-Stick ist zu klein. Für diese Auswahl werden mindestens "
                f"{required_bytes // (1024**3)} GiB benötigt. Es wurde nichts formatiert."
            )
        if _disk_size(SOURCE_DEVICE) != job.image_size:
            raise ValueError("Die Größe der System-microSD hat sich geändert.")

        _set_job(state="formatieren", phase="formatieren", message="USB-Stick wird als exFAT vorbereitet.")
        _format_usb(device)
        available_bytes = shutil.disk_usage(VOLUME_PATH).free
        if available_bytes < required_bytes:
            raise ValueError("Nach dem Formatieren steht nicht genug freier Platz für den sicheren Image-Ablauf zur Verfügung.")
        if not _printer_safe():
            raise ValueError("Der Druckerstatus ist nicht sicher. Die Sicherung wurde nicht gestartet.")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_hostname = re.sub(r"[^A-Za-z0-9._-]+", "-", hostname).strip(".-") or "klipper"
        base = f"{safe_hostname}-{stamp}-sdkarte"
        full_path = VOLUME_PATH / (f"{base}-voll.img" if job.shrink else f"{base}.img")
        _set_job(state="sichern", phase="sichern", message="Vollständiges Karten-Image wird auf den USB-Stick geschrieben.", sent_bytes=0)
        digest = hashlib.sha256()
        next_printer_check = time.monotonic()
        source_fd = os.open(SOURCE_DEVICE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            if not stat.S_ISBLK(os.fstat(source_fd).st_mode):
                raise OSError("Die System-microSD ist kein Blockgerät.")
            with full_path.open("xb", buffering=0) as output:
                copied = 0
                while copied < job.image_size:
                    if time.monotonic() >= next_printer_check:
                        if not _printer_safe():
                            raise OSError("Druckerstatus ist nicht mehr sicher. Image-Datei ist unvollständig.")
                        next_printer_check = time.monotonic() + PRINTER_RECHECK_SECONDS
                    chunk = os.read(source_fd, min(CHUNK_BYTES, job.image_size - copied))
                    if not chunk:
                        raise OSError(f"Die microSD lieferte nur {copied} von {job.image_size} Bytes.")
                    output.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    _set_job(sent_bytes=copied)
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.close(source_fd)
        _set_job(sha256=digest.hexdigest())

        _set_job(state="verifizieren", phase="verifizieren", message="Geschriebenes Image wird vom USB-Stick zurückgelesen und geprüft.")
        if _hash_file(full_path) != digest.hexdigest():
            raise OSError("Der SHA-256-Vergleich der geschriebenen USB-Image-Datei ist fehlgeschlagen.")

        if job.shrink:
            _set_job(state="schrumpfen", phase="schrumpfen", message="PiShrink verkleinert jetzt die Dateisystem- und Partitionsgröße.")
            final_path = VOLUME_PATH / f"{base}-geshrinkt.img"
            _run([str(PISHRINK_PATH), "-ns", str(full_path), str(final_path)], timeout=60 * 60 * 4)
            if not final_path.is_file() or final_path.stat().st_size <= 0 or final_path.stat().st_size >= job.image_size:
                raise OSError("PiShrink hat kein kleineres, gültiges Image erzeugt.")
            final_hash = _hash_file(final_path)
            files = [{"filename": final_path.name, "size_bytes": final_path.stat().st_size, "sha256": final_hash, "kind": "geshrinkt"}]
            if job.keep_full:
                files.insert(0, {"filename": full_path.name, "size_bytes": full_path.stat().st_size, "sha256": digest.hexdigest(), "kind": "vollständig"})
            else:
                full_path.unlink()
        else:
            files = [{"filename": full_path.name, "size_bytes": full_path.stat().st_size, "sha256": digest.hexdigest(), "kind": "vollständig"}]
        _set_job(state="bereit", phase="fertig", message="Image ist auf dem USB-Stick gespeichert und kann heruntergeladen werden.", files=files)
    except Exception as exc:
        files = []
        if full_path and full_path.is_file() and full_path.stat().st_size == job.image_size and job.sha256:
            files.append({
                "filename": full_path.name, "size_bytes": full_path.stat().st_size,
                "sha256": job.sha256, "kind": "vollständig",
            })
        _set_job(
            state="fehler", phase="fehler", message="USB-Image konnte nicht sicher erstellt werden.",
            error=str(exc), files=files,
        )


def _job_data(job: MediaJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id, "state": job.state, "phase": job.phase, "message": job.message,
        "image_size_bytes": job.image_size, "sent_bytes": job.sent_bytes,
        "sha256": job.sha256, "error": job.error, "files": job.files,
        "shrink": job.shrink, "keep_full": job.keep_full,
    }


class MediaHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        if _worker_uid is None:
            self._reply({"error": "worker_not_ready"})
            return
        _, peer_uid, _ = struct.unpack(
            "3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
        if peer_uid != _worker_uid:
            self._reply({"error": "forbidden"})
            return
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            self._reply({"error": "invalid_request"})
            return
        try:
            request = json.loads(raw)
            action = request.get("action")
            if action == "devices":
                self._reply({"devices": _devices()})
            elif action == "start":
                self._start(request)
            elif action == "status":
                self._status(request)
            elif action == "download":
                self._download(request)
            elif action == "eject":
                self._eject(request)
            else:
                self._reply({"error": "unsupported_action"})
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._reply({"error": str(exc) or "media_operation_failed"})

    def _start(self, request: dict[str, Any]) -> None:
        global _job
        if request.get("confirmed") is not True:
            raise ValueError("Bestätige zuerst, dass der USB-Stick vollständig formatiert werden darf.")
        path = request.get("device_path")
        if not isinstance(path, str):
            raise ValueError("USB-Ziel fehlt.")
        device = _selected_device(path)
        with _lock:
            if _job and _job.state in {"formatieren", "sichern", "schrumpfen"}:
                raise ValueError("Auf dem USB-Stick läuft bereits ein Auftrag.")
        image_size = _disk_size(SOURCE_DEVICE)
        if image_size <= 0:
            raise ValueError("Größe der System-microSD nicht verfügbar.")
        shrink = request.get("shrink") is True
        keep_full = shrink and request.get("keep_full") is True
        required_bytes = image_size * (2 if shrink else 1) + max(2 * 1024**3, image_size // 10)
        if int(device["size_bytes"]) < required_bytes:
            raise ValueError(
                "Der USB-Stick ist zu klein. Für diese Auswahl werden mindestens "
                f"{required_bytes // (1024**3)} GiB benötigt. Es wurde nichts formatiert."
            )
        job = MediaJob(
            job_id=secrets.token_urlsafe(20),
            state="wird vorbereitet", phase="vorbereitung", message="USB-Ziel wird geprüft.",
            image_size=image_size, source_device=SOURCE_DEVICE, shrink=shrink, keep_full=keep_full,
        )
        with _lock:
            _job = job
        threading.Thread(target=_run_job, args=(job, device, socket.gethostname()), daemon=True).start()
        self._reply({"job": _job_data(job), "device": device})

    def _status(self, request: dict[str, Any]) -> None:
        with _lock:
            job = _job
            if not job or job.job_id != request.get("job_id"):
                raise ValueError("USB-Auftrag nicht gefunden.")
            self._reply({"job": _job_data(job)})

    def _download(self, request: dict[str, Any]) -> None:
        name = request.get("filename")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Ungültiger Dateiname.")
        with _lock:
            job = _job
            file_info = next((item for item in job.files if item["filename"] == name), None) if job else None
            if not job or job.state not in {"bereit", "fehler"} or not file_info:
                raise ValueError("Image-Download ist nicht bereit.")
            if job.active_downloads:
                raise ValueError("Ein USB-Image wird bereits zum PC übertragen.")
            job.active_downloads += 1
        path = VOLUME_PATH / name
        if path.parent != VOLUME_PATH or not path.is_file() or path.stat().st_size != file_info["size_bytes"]:
            with _lock:
                job.active_downloads -= 1
            raise ValueError("Die Image-Datei auf dem USB-Stick ist nicht verfügbar.")
        try:
            self._reply({"filename": name, "size_bytes": file_info["size_bytes"], "sha256": file_info["sha256"]})
            with path.open("rb") as image:
                while True:
                    chunk = image.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            with _lock:
                job.active_downloads -= 1

    def _eject(self, request: dict[str, Any]) -> None:
        with _lock:
            if not _job or _job.job_id != request.get("job_id"):
                raise ValueError("USB-Auftrag nicht gefunden.")
            if _job and _job.state in {"formatieren", "sichern", "schrumpfen"}:
                raise ValueError("Der USB-Stick kann während eines laufenden Auftrags nicht ausgeworfen werden.")
            if _job and _job.active_downloads:
                raise ValueError("Warte, bis der Browser-Download abgeschlossen ist, bevor du den Stick auswirfst.")
        if os.path.ismount(str(VOLUME_PATH)):
            _run(["sync"])
            _run(["umount", "--", str(VOLUME_PATH)])
        self._reply({"ejected": True})

    def _reply(self, result: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(result).encode("utf-8") + b"\n")


class MediaServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    global _worker_uid
    _worker_uid = pwd.getpwnam("kibm").pw_uid
    SOCKET_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    VOLUME_PATH.mkdir(mode=0o750, parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    with MediaServer(str(SOCKET_PATH), MediaHandler) as server:
        os.chown(SOCKET_PATH, 0, _worker_uid)
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
