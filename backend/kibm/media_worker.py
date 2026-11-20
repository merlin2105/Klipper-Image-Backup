from __future__ import annotations

import hashlib
import grp
import json
import logging
import os
import pwd
import re
import secrets
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
from typing import Any, Callable, Optional

SOCKET_PATH = Path("/run/kibm-media/worker.sock")
VOLUME_PATH = Path("/run/kibm-media/volume")
SCAN_PATH = Path("/run/kibm-media/scan")
# Root-owned; read by `kibm-admin update-run` so the update installs exactly the
# commit the user confirmed in the dashboard.
UPDATE_COMMIT_PATH = Path("/run/kibm-media/update-commit")
SOURCE_DEVICE = "/dev/mmcblk0"
PISHRINK_PATH = Path("/opt/klipper-image-backup-manager/tools/pishrink.sh")
BLOCKGETSIZE64 = 0x80081272
CHUNK_BYTES = 4 * 1024 * 1024
USB_FORMAT_HEADROOM_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BYTES = 4096
PRINTER_RECHECK_SECONDS = 10
BUSY_STATES = {"wird vorbereitet", "formatieren", "sichern", "verifizieren", "schrumpfen"}
logger = logging.getLogger("kibm.media")


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
    verify_image: bool = True
    created_at: float = field(default_factory=time.time)
    sent_bytes: int = 0
    verified_bytes: int = 0
    shrink_bytes: int = 0
    shrink_total_bytes: int = 0
    sha256: Optional[str] = None
    error: Optional[str] = None
    files: list[dict[str, Any]] = field(default_factory=list)
    active_downloads: int = 0


_lock = threading.RLock()
_job: Optional[MediaJob] = None
_worker_uid: Optional[int] = None
_worker_gid: Optional[int] = None
_listed_images: dict[str, dict[str, Any]] = {}
_image_classification_cache: dict[tuple[str, int, int], dict[str, Any]] = {}


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def _ensure_update_idle() -> None:
    state = _run(["systemctl", "show", "kibm-update.service", "--property=ActiveState", "--value"], timeout=10).stdout.strip()
    if state in {"active", "activating", "deactivating"}:
        raise ValueError("KIBM wird gerade aktualisiert. Bitte warte, bis der Vorgang abgeschlossen ist.")


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


def _devices(flat: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    if flat is None:
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
        "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,RM,RO,TRAN,MAJ:MIN,UUID",
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
                "uuid": item.get("uuid"),
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


def _image_volumes() -> list[dict[str, Any]]:
    flat = _all_devices()
    disks = {disk["path"]: disk for disk in _devices(flat)}
    volumes = []
    for item in flat:
        disk = disks.get(item["parent"] or item["path"])
        if not disk or item["type"] not in {"disk", "part"} or item["filesystem"] != "exfat" or item["read_only"]:
            continue
        if set(item["mountpoints"]) - {str(VOLUME_PATH)}:
            continue
        try:
            sequence = (Path("/sys/class/block") / disk["name"] / "diskseq").read_text().strip()
        except OSError:
            sequence = None
        try:
            inode = os.stat(item["path"], follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISBLK(inode.st_mode):
            volumes.append({
                **item, "device": disk, "rdev": inode.st_rdev,
                "identity": (item["path"], item["uuid"], item["size_bytes"], sequence),
            })
    return volumes


def _image_id(volume: dict[str, Any], path: Path, info: os.stat_result) -> str:
    identity = (volume["identity"], path.name, info.st_size, info.st_mtime_ns)
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _mounted_volume(path: Path, volume: dict[str, Any]) -> bool:
    return path.is_mount() and path.stat().st_dev == volume["rdev"]


def _classify_image(path: Path, image_size: int, mtime_ns: int, *, use_cache: bool = True) -> dict[str, Any]:
    cache_key = (path.name, image_size, mtime_ns)
    if use_cache and cache_key in _image_classification_cache:
        return dict(_image_classification_cache[cache_key])
    try:
        result = _inspect_image(path, image_size, mtime_ns)
    except (OSError, ValueError, IndexError, struct.error):
        result = {"status": "unknown", "estimated_savings_bytes": 0}
    if use_cache:
        if len(_image_classification_cache) >= 128:
            _image_classification_cache.clear()
        _image_classification_cache[cache_key] = result
    return result


def _inspect_image(path: Path, image_size: int, mtime_ns: int) -> dict[str, Any]:
    # Parse fixed MBR/ext4 fields directly; do not run filesystem tools as root
    # against arbitrary images discovered on a USB drive.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != image_size or info.st_mtime_ns != mtime_ns:
            return {"status": "unknown", "estimated_savings_bytes": 0}
        mbr = os.pread(fd, 512, 0)
        if len(mbr) != 512 or mbr[510:512] != b"\x55\xaa":
            return {"status": "unknown", "estimated_savings_bytes": 0}
        partitions = []
        for index in range(4):
            entry = mbr[446 + index * 16:462 + index * 16]
            first_sector = struct.unpack_from("<I", entry, 8)[0]
            sector_count = struct.unpack_from("<I", entry, 12)[0]
            if not first_sector or not sector_count:
                continue
            partitions.append((first_sector * 512, sector_count * 512))
        for start, partition_size in sorted(partitions, reverse=True):
            superblock = os.pread(fd, 1024, start + 1024)
            if len(superblock) != 1024 or struct.unpack_from("<H", superblock, 56)[0] != 0xEF53:
                continue
            log_block_size = struct.unpack_from("<I", superblock, 24)[0]
            if log_block_size > 6:
                return {"status": "unknown", "estimated_savings_bytes": 0}
            block_size = 1024 << log_block_size
            blocks = struct.unpack_from("<I", superblock, 4)[0]
            free_blocks = struct.unpack_from("<I", superblock, 12)[0]
            incompat_features = struct.unpack_from("<I", superblock, 96)[0]
            if incompat_features & 0x80:
                blocks |= struct.unpack_from("<I", superblock, 0x150)[0] << 32
                free_blocks |= struct.unpack_from("<I", superblock, 0x158)[0] << 32
            filesystem_size = blocks * block_size
            if not blocks or filesystem_size > partition_size or start + partition_size > image_size + 512:
                return {"status": "unknown", "estimated_savings_bytes": 0}
            safety_blocks = 5000 if free_blocks > 5000 else 1000 if free_blocks > 1000 else 100 if free_blocks > 100 else 0
            free_space = max(0, free_blocks - safety_blocks) * block_size
            partition_slack = partition_size - filesystem_size
            image_tail = max(0, image_size - start - partition_size)
            savings = free_space + partition_slack + image_tail
            status = "shrinkable" if savings >= 256 * 1024 * 1024 else "already_compact"
            return {"status": status, "estimated_savings_bytes": savings}
        return {"status": "unknown", "estimated_savings_bytes": 0}
    finally:
        os.close(fd)


def _mount_image_volume(volume: dict[str, Any], target: Path, mode: str) -> None:
    target.mkdir(mode=0o750, exist_ok=True)
    if target.is_mount():
        raise ValueError("Der USB-Einhängepunkt ist bereits belegt.")
    _run(["mount", "-t", "exfat", "-o", f"{mode},nosuid,nodev,noexec", volume["path"], str(target)], timeout=4)
    if not _mounted_volume(target, volume):
        _run(["umount", "--", str(target)])
        raise ValueError("Der USB-Stick wurde gewechselt.")


def _existing_images() -> list[dict[str, Any]]:
    images = []
    for volume in _image_volumes():
        try:
            already_mounted = _mounted_volume(VOLUME_PATH, volume)
            directory = VOLUME_PATH if already_mounted else SCAN_PATH
            if not already_mounted:
                _mount_image_volume(volume, directory, "ro")
            try:
                for path in directory.iterdir():
                    if path.suffix.lower() != ".img" or path.name.startswith("."):
                        continue
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_size > 0:
                        shrinkability = _classify_image(path, info.st_size, info.st_mtime_ns)
                        images.append({
                            "id": _image_id(volume, path, info), "filename": path.name,
                            "size_bytes": info.st_size, "device": volume["device"]["path"],
                            "partition": volume["path"], "shrinkability": shrinkability, "volume": volume,
                        })
                    if len(images) >= 100:
                        return images
            finally:
                if not already_mounted:
                    _run(["umount", "--", str(directory)], timeout=4)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return images


def _printer_active() -> bool:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:7125/printer/objects/query?print_stats", timeout=2
        ) as response:
            stats = json.loads(response.read(1024 * 1024)).get("result", {}).get("status", {}).get("print_stats")
        # Backup requires an explicit confirmation. Without Moonraker there is
        # no live status; a reported active or paused print still blocks it.
        return bool(isinstance(stats, dict) and stats.get("state", "").lower() in {"printing", "paused"})
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


def _hash_file(path: Path, on_read: Optional[Callable[[int], None]] = None) -> str:
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as image:
        while True:
            chunk = image.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            if on_read is not None:
                bytes_read += len(chunk)
                on_read(bytes_read)
    return digest.hexdigest()


def _usb_image_bytes(image_size: int, shrink: bool) -> int:
    # PiShrink copies the whole image before resizing its working copy.
    return image_size * (2 if shrink else 1)


def _check_usb_capacity(device: dict[str, Any], image_size: int, shrink: bool) -> None:
    required = _usb_image_bytes(image_size, shrink) + USB_FORMAT_HEADROOM_BYTES
    if int(device["size_bytes"]) < required:
        raise ValueError(
            f"Der USB-Stick ist zu klein. Diese Auswahl benötigt rund {required / 1024**3:.2f} GiB. "
            "Es wurde nichts formatiert."
        )


def _set_job(**values: Any) -> None:
    with _lock:
        if _job:
            for key, value in values.items():
                setattr(_job, key, value)


def _copy_for_shrink(source: Path, target: Path) -> None:
    copied = 0
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(CHUNK_BYTES):
            writer.write(chunk)
            copied += len(chunk)
            _set_job(shrink_bytes=copied)


def _shrink_image(job: MediaJob, full_path: Path, final_path: Path) -> dict[str, Any]:
    working_path = VOLUME_PATH / f".{job.job_id}.img.part"
    _set_job(
        state="schrumpfen", phase="kopie", shrink_total_bytes=job.image_size,
        message="Arbeitskopie für PiShrink wird erstellt.",
    )
    try:
        _copy_for_shrink(full_path, working_path)
        _set_job(phase="verkleinern", message="PiShrink prüft und verkleinert Dateisystem und Partition.")
        _run([str(PISHRINK_PATH), "-nso", str(working_path)], timeout=60 * 60 * 4)
        final_size = working_path.stat().st_size
        if not 0 < final_size < job.image_size:
            raise OSError("PiShrink hat kein kleineres, gültiges Image erzeugt.")
        _set_job(
            phase="pruefsumme", shrink_bytes=0, shrink_total_bytes=final_size,
            message="Prüfsumme des verkleinerten Images wird berechnet.",
        )
        final_hash = _hash_file(working_path, lambda count: _set_job(shrink_bytes=count))
        with working_path.open("rb") as image:
            os.fsync(image.fileno())
        working_path.rename(final_path)
    finally:
        working_path.unlink(missing_ok=True)
    return {"filename": final_path.name, "size_bytes": final_size, "sha256": final_hash, "kind": "geshrinkt"}


def _run_existing_shrink(job: MediaJob, image: dict[str, Any]) -> None:
    try:
        if _printer_active():
            raise ValueError("Moonraker meldet einen aktiven Druckauftrag.")
        expected = image["volume"]["identity"]
        volume = next((item for item in _image_volumes() if item["identity"] == expected), None)
        if volume is None:
            raise ValueError("Der USB-Stick wurde gewechselt oder ist nicht mehr verfügbar.")
        if not _mounted_volume(VOLUME_PATH, volume):
            if VOLUME_PATH.is_mount():
                _run(["umount", "--", str(VOLUME_PATH)])
            _mount_image_volume(volume, VOLUME_PATH, "rw")
        if not any(item["identity"] == expected for item in _image_volumes()):
            raise ValueError("Der USB-Stick wurde beim Einbinden gewechselt.")
        source = VOLUME_PATH / image["filename"]
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or _image_id(volume, source, info) != image["id"]:
            raise ValueError("Die Image-Datei wurde seit der Auswahl verändert. Wähle sie erneut aus.")
        shrinkability = _classify_image(source, info.st_size, info.st_mtime_ns, use_cache=False)
        if shrinkability["status"] != "shrinkable":
            raise ValueError("Das Image ist bereits kompakt oder sein Dateisystem lässt sich nicht sicher prüfen.")
        filesystem = os.statvfs(VOLUME_PATH)
        if filesystem.f_bavail * filesystem.f_frsize < job.image_size:
            raise ValueError("Auf dem Stick fehlt freier Speicherplatz für eine vollständige Arbeitskopie.")
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-")[:100] or "usb-image"
        final_path = VOLUME_PATH / f"{base}-{job.job_id}-geshrinkt.img"
        result = _shrink_image(job, source, final_path)
        _set_job(state="bereit", phase="fertig", message="Verkleinerte Kopie ist auf USB gespeichert.", files=[result])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        _set_job(state="fehler", phase="fehler", message="USB-Image konnte nicht verkleinert werden.", error=str(exc))


def _run_job(job: MediaJob, device: dict[str, Any], hostname: str) -> None:
    full_path: Optional[Path] = None
    full_image_ready = False
    try:
        if _printer_active():
            raise ValueError("Moonraker meldet einen aktiven Druck. Es wurde noch nichts formatiert.")
        current_device = _selected_device(device["path"])
        if current_device["major_minor"] != device["major_minor"] or current_device["size_bytes"] != device["size_bytes"]:
            raise ValueError("Der USB-Stick wurde seit der Auswahl gewechselt. Es wurde nichts formatiert.")
        if _disk_size(SOURCE_DEVICE) != job.image_size:
            raise ValueError("Die Größe der System-microSD hat sich geändert.")
        _check_usb_capacity(current_device, job.image_size, job.shrink)

        _set_job(state="formatieren", phase="formatieren", message="USB-Stick wird als exFAT vorbereitet.")
        _format_usb(device)
        filesystem = os.statvfs(VOLUME_PATH)
        cluster_bytes = filesystem.f_frsize
        allocated_image_bytes = ((job.image_size + cluster_bytes - 1) // cluster_bytes) * cluster_bytes
        if filesystem.f_bavail * cluster_bytes < _usb_image_bytes(allocated_image_bytes, job.shrink):
            raise ValueError("Nach dem Formatieren steht nicht genug freier Platz für den sicheren Image-Ablauf zur Verfügung.")
        if _printer_active():
            raise ValueError("Moonraker meldet einen aktiven Druck. Die Sicherung wurde nicht gestartet.")

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
                        if _printer_active():
                            raise OSError("Moonraker meldet einen aktiven Druck. Image-Datei ist unvollständig.")
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

        if job.verify_image:
            _set_job(state="verifizieren", phase="verifizieren", message="Geschriebenes Image wird vom USB-Stick zurückgelesen und geprüft.")
            if _hash_file(full_path, lambda count: _set_job(verified_bytes=count)) != digest.hexdigest():
                raise OSError("Der SHA-256-Vergleich der geschriebenen USB-Image-Datei ist fehlgeschlagen.")
        full_image_ready = True

        if job.shrink:
            final_path = VOLUME_PATH / f"{base}-geshrinkt.img"
            files = [_shrink_image(job, full_path, final_path)]
            if job.keep_full:
                files.insert(0, {"filename": full_path.name, "size_bytes": full_path.stat().st_size, "sha256": digest.hexdigest(), "kind": "vollständig"})
            else:
                full_path.unlink()
        else:
            files = [{"filename": full_path.name, "size_bytes": full_path.stat().st_size, "sha256": digest.hexdigest(), "kind": "vollständig"}]
        _set_job(state="bereit", phase="fertig", message="Image ist auf dem USB-Stick gespeichert und kann heruntergeladen werden.", files=files)
    except Exception as exc:
        files = []
        if full_image_ready and full_path and full_path.is_file() and full_path.stat().st_size == job.image_size:
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
        "image_size_bytes": job.image_size, "sent_bytes": job.sent_bytes, "verified_bytes": job.verified_bytes,
        "shrink_bytes": job.shrink_bytes, "shrink_total_bytes": job.shrink_total_bytes,
        "sha256": job.sha256, "error": job.error, "files": job.files,
        "shrink": job.shrink, "keep_full": job.keep_full, "verify_image": job.verify_image,
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
        action = "unknown"
        try:
            request = json.loads(raw)
            action = request.get("action")
            if action == "devices":
                self._reply({"devices": _devices()})
            elif action == "images":
                self._list_images()
            elif action == "start":
                self._start(request)
            elif action == "shrink_existing":
                self._start_existing(request)
            elif action == "delete_image":
                self._delete_image(request)
            elif action == "status":
                self._status(request)
            elif action == "download":
                self._download(request)
            elif action == "eject":
                self._eject(request)
            elif action == "update_status":
                self._update_status()
            elif action == "update_start":
                self._update_start(request)
            else:
                self._reply({"error": "unsupported_action"})
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            detail = str(exc) or "media_operation_failed"
            if isinstance(exc, subprocess.CalledProcessError):
                output = (exc.stderr or exc.stdout or "").strip()
                if output:
                    detail = f"{detail}: {output}"
            if action == "update_start":
                logger.error("Update start rejected: %s", detail)
            self._reply({"error": detail})

    def _list_images(self) -> None:
        with _lock:
            if _job and _job.state in BUSY_STATES:
                self._reply({"images": []})
                return
            images = _existing_images()
            _listed_images.clear()
            _listed_images.update((item["id"], item) for item in images)
            self._reply({"images": [
                {key: value for key, value in item.items() if key != "volume"} for item in images
            ]})

    def _start_existing(self, request: dict[str, Any]) -> None:
        global _job
        _ensure_update_idle()
        with _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Auf dem USB-Stick läuft bereits ein Auftrag oder Download.")
            image = _listed_images.get(request.get("image_id"))
            if not image:
                raise ValueError("Die Image-Datei ist nicht mehr verfügbar. Wähle sie erneut aus.")
            if image.get("shrinkability", {}).get("status") != "shrinkable":
                raise ValueError("Dieses Image ist bereits kompakt oder kann nicht sicher geprüft werden.")
            volume = image["volume"]
            job = MediaJob(
                job_id=secrets.token_hex(10), state="wird vorbereitet", phase="vorbereitung",
                message="Image auf USB wird geöffnet.", image_size=image["size_bytes"],
                source_device=volume["path"], shrink=True, keep_full=True, verify_image=False,
            )
            _job = job
        threading.Thread(target=_run_existing_shrink, args=(job, image), daemon=True).start()
        self._reply({"job": _job_data(job), "device": volume["device"]})

    def _delete_image(self, request: dict[str, Any]) -> None:
        _ensure_update_idle()
        with _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Warte, bis der laufende USB-Auftrag und alle Downloads abgeschlossen sind.")
            image = _listed_images.get(request.get("image_id"))
            if not image:
                raise ValueError("Das Image ist nicht mehr in der aktuellen USB-Liste. Scanne den Stick erneut.")
            filename = image.get("filename")
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.lower().endswith(".img"):
                raise ValueError("Es kann nur eine einzelne .img-Datei gelöscht werden.")
            expected = image["volume"]["identity"]
            volume = next((item for item in _image_volumes() if item["identity"] == expected), None)
            if volume is None:
                raise ValueError("Der USB-Stick wurde gewechselt oder ist nicht mehr verfügbar.")
            if not _mounted_volume(VOLUME_PATH, volume):
                if VOLUME_PATH.is_mount():
                    _run(["umount", "--", str(VOLUME_PATH)])
                _mount_image_volume(volume, VOLUME_PATH, "rw")
            if not any(item["identity"] == expected for item in _image_volumes()):
                raise ValueError("Der USB-Stick wurde beim Einbinden gewechselt.")
            source = VOLUME_PATH / filename
            info = source.lstat()
            if not stat.S_ISREG(info.st_mode) or _image_id(volume, source, info) != image["id"]:
                raise ValueError("Die Image-Datei wurde seit der Auswahl verändert. Scanne den Stick erneut.")
            source.unlink()
            directory_fd = os.open(VOLUME_PATH, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            _listed_images.pop(image["id"], None)
            self._reply({"deleted": filename})

    def _start(self, request: dict[str, Any]) -> None:
        global _job
        _ensure_update_idle()
        if request.get("confirmed") is not True:
            raise ValueError("Bestätige zuerst, dass der USB-Stick vollständig formatiert werden darf.")
        path = request.get("device_path")
        if not isinstance(path, str):
            raise ValueError("USB-Ziel fehlt.")
        device = _selected_device(path)
        image_size = _disk_size(SOURCE_DEVICE)
        if image_size <= 0:
            raise ValueError("Größe der System-microSD nicht verfügbar.")
        shrink = request.get("shrink") is True
        keep_full = shrink and request.get("keep_full") is True
        _check_usb_capacity(device, image_size, shrink)
        job = MediaJob(
            job_id=secrets.token_urlsafe(20),
            state="wird vorbereitet", phase="vorbereitung", message="USB-Ziel wird geprüft.",
            image_size=image_size, source_device=SOURCE_DEVICE, shrink=shrink, keep_full=keep_full,
            verify_image=request.get("verify_image", True) is not False,
        )
        with _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Auf dem USB-Stick läuft bereits ein Auftrag oder Download.")
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
        _ensure_update_idle()
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
        _ensure_update_idle()
        with _lock:
            if _job and _job.state in BUSY_STATES:
                raise ValueError("Der USB-Stick kann während eines laufenden Auftrags nicht ausgeworfen werden.")
            if _job and _job.active_downloads:
                raise ValueError("Warte, bis der Browser-Download abgeschlossen ist, bevor du den Stick auswirfst.")
            if os.path.ismount(str(VOLUME_PATH)):
                source = _run(["findmnt", "-n", "-o", "SOURCE", "--target", str(VOLUME_PATH)], timeout=5).stdout.strip()
                try:
                    _run(["sync"], timeout=30)
                    _run(["umount", "--", str(VOLUME_PATH)], timeout=15)
                except subprocess.SubprocessError:
                    if not source.startswith("/dev/") or Path(source).exists():
                        raise
                    _run(["umount", "--lazy", "--", str(VOLUME_PATH)], timeout=15)
                if os.path.ismount(str(VOLUME_PATH)):
                    raise ValueError("Der USB-Einhängepunkt ist noch aktiv. Versuche den sicheren Auswurf erneut.")
        self._reply({"ejected": True})

    def _update_status(self) -> None:
        output = _run([
            "systemctl", "show", "kibm-update.service", "--property=LoadState", "--property=ActiveState", "--property=Result",
            "--property=ExecMainStatus", "--property=ExecMainStartTimestamp",
        ], timeout=10).stdout
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        loaded = fields.get("LoadState") == "loaded"
        active = fields.get("ActiveState", "unknown")
        result = fields.get("Result", "")
        started = bool(fields.get("ExecMainStartTimestamp", ""))
        if not loaded:
            state = "unavailable"
        elif active in {"activating", "active", "deactivating"}:
            state = "running"
        elif active == "failed" or (started and result not in {"", "success"}):
            state = "failed"
        elif started and result == "success":
            state = "success"
        else:
            state = "idle"
        self._reply({
            "available": loaded, "state": state, "exit_status": fields.get("ExecMainStatus"),
            "usb_mounted": os.path.ismount(str(VOLUME_PATH)),
        })

    def _update_start(self, request: dict[str, Any]) -> None:
        commit = request.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Die bestätigte Update-Version ist ungültig.")
        with _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Warte, bis das USB-Image fertig ist und alle Downloads abgeschlossen sind.")
            if os.path.ismount(str(VOLUME_PATH)):
                raise ValueError("Bitte den USB-Stick vor dem Update sicher auswerfen.")
        status = _run(["systemctl", "show", "kibm-update.service", "--property=ActiveState", "--value"], timeout=10).stdout.strip()
        if status in {"active", "activating", "deactivating"}:
            raise ValueError("Ein Update läuft bereits.")
        staged = UPDATE_COMMIT_PATH.with_name(UPDATE_COMMIT_PATH.name + ".tmp")
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(commit + "\n")
        os.chmod(staged, 0o600)
        staged.replace(UPDATE_COMMIT_PATH)
        try:
            _run(["systemctl", "start", "--no-block", "kibm-update.service"], timeout=10)
        except BaseException:
            UPDATE_COMMIT_PATH.unlink(missing_ok=True)
            raise
        self._reply({"state": "running"})

    def _reply(self, result: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(result).encode("utf-8") + b"\n")


class MediaServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    global _worker_uid, _worker_gid
    _worker_uid = pwd.getpwnam("kibm").pw_uid
    _worker_gid = grp.getgrnam("kibm").gr_gid
    SOCKET_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    VOLUME_PATH.mkdir(mode=0o750, parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    with MediaServer(str(SOCKET_PATH), MediaHandler) as server:
        os.chown(SOCKET_PATH, 0, _worker_gid)
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
