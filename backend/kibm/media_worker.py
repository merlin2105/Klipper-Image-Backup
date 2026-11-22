from __future__ import annotations

import errno
import fcntl
import hashlib
import grp
import json
import logging
import os
import pwd
import re
import secrets
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

try:
    from . import nas, partitions, printer_status
except ImportError:  # started as a script by systemd: load the sibling modules explicitly
    import importlib.util

    def _sibling(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(f"kibm_{name}", Path(__file__).with_name(f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        # dataclasses look up their module in sys.modules while the class is created.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    partitions = _sibling("partitions")
    nas = _sibling("nas")
    printer_status = _sibling("printer_status")

RUN_DIR = Path("/run/kibm-media")
SOCKET_PATH = Path("/run/kibm-media/worker.sock")
NAS_MOUNT_PATH = RUN_DIR / "nas"
NAS_STATE_DIR = Path("/var/lib/kibm-media")
NAS_CONFIG_PATH = NAS_STATE_DIR / "nas.json"
NAS_CREDENTIALS_PATH = NAS_STATE_DIR / "nas-credentials"
NAS_LOCK_WAIT_SECONDS = 10
MOUNTINFO_PATH = Path("/proc/self/mountinfo")
VOLUME_PATH = Path("/run/kibm-media/volume")
SCAN_PATH = Path("/run/kibm-media/scan")
# Root-owned; read by `kibm-admin update-run` so the update installs exactly the
# commit the user confirmed in the dashboard.
UPDATE_COMMIT_PATH = Path("/run/kibm-media/update-commit")
SOURCE_DEVICE = "/dev/mmcblk0"
# ext4 keeps an error counter per filesystem in its superblock; it survives reboots.
EXT4_SYSFS = Path("/sys/fs/ext4")
PISHRINK_PATH = Path("/opt/klipper-image-backup-manager/tools/pishrink.sh")
BLOCKGETSIZE64 = 0x80081272
# Backup progress on the printer (Klipper status line, KlipperScreen banner).
PRINTER_STATUS = printer_status.Notifier(printer_status.moonraker_send, printer_status.moonraker_has_respond)
FIFREEZE = 0xC0045877
FITHAW = 0xC0045878
CHUNK_BYTES = 4 * 1024 * 1024
USB_FORMAT_HEADROOM_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BYTES = 4096
PRINTER_RECHECK_SECONDS = 10
BUSY_STATES = {"wird vorbereitet", "formatieren", "sichern", "verifizieren", "reparieren", "schrumpfen"}
E2FSCK_TIMEOUT_SECONDS = 60 * 60
logger = logging.getLogger("kibm.media")


@dataclass
class MediaJob:
    job_id: str
    state: str
    phase: str
    message: str
    # image_size is what gets imaged (up to the end of the last partition);
    # device_size is the whole card and must not change during the job.
    image_size: int
    source_device: str
    shrink: bool
    keep_full: bool
    verify_image: bool = True
    device_size: int = 0
    created_at: float = field(default_factory=time.time)
    sent_bytes: int = 0
    verified_bytes: int = 0
    shrink_bytes: int = 0
    shrink_total_bytes: int = 0
    sha256: Optional[str] = None
    error: Optional[str] = None
    files: list[dict[str, Any]] = field(default_factory=list)
    active_downloads: int = 0
    target: str = "usb"
    cancel_requested: bool = False


class JobCancelled(Exception):
    """The user cancelled the running job."""


_lock = threading.RLock()
# Serialises USB scans against jobs that mount or change the stick. Always
# taken before _lock, so status requests (only _lock) never wait for a scan.
_scan_lock = threading.Lock()
# Bumped whenever KIBM itself changes files on a stick; together with the
# volume identities it decides whether the cached image list is still valid.
_images_generation = 0
_images_cache: Optional[tuple[Any, list[dict[str, Any]]]] = None
_job: Optional[MediaJob] = None
_worker_uid: Optional[int] = None
_worker_gid: Optional[int] = None
_listed_images: dict[str, dict[str, Any]] = {}
_image_classification_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
# Serialises everything that mounts or unmounts NAS_MOUNT_PATH.
_nas_lock = threading.Lock()


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def _check_cancel() -> None:
    with _lock:
        if _job and _job.cancel_requested:
            raise JobCancelled()


def _progress(**values: Any) -> None:
    """Report progress of the running job; stops it here if the user cancelled."""
    _check_cancel()
    _set_job(**values)


def _run_cancellable(
    args: list[str], timeout: int, check: bool = False, poll_seconds: float = 1,
) -> subprocess.CompletedProcess[str]:
    """Like _run for long tools (e2fsck, PiShrink) that a cancel must be able to stop.

    The tool runs in its own process group, so its helpers (e2fsck, resize2fs inside
    PiShrink) stop with it.
    """
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=poll_seconds)
                break
            except subprocess.TimeoutExpired:
                _check_cancel()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(args, timeout) from None
    except BaseException:
        _stop_group(process)
        raise
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _stop_group(process: subprocess.Popen[str]) -> None:
    # SIGTERM first so PiShrink can run its cleanup trap; SIGKILL if it hangs.
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, None)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.communicate(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            continue


def _detach_loops(path: Path) -> None:
    """Detach loop devices a stopped tool left on `path`, so its space is freed."""
    try:
        listing = _run(["losetup", "--noheadings", "--output", "NAME", "--associated", str(path)]).stdout
        for loop in listing.split():
            _run(["losetup", "--detach", loop])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not detach loop devices of %s: %s", path, exc)


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


def _capture_size(path: str, device_size: int) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return partitions.capture_bytes(os.pread(fd, partitions.SECTOR_BYTES, 0), device_size)
    finally:
        os.close(fd)


def _write_all(output: Any, chunk: bytes) -> None:
    # An unbuffered write may store fewer bytes than given.
    view = memoryview(chunk)
    while view:
        written = output.write(view)
        if not written:
            raise OSError("Der USB-Stick hat keine Daten mehr angenommen.")
        view = view[written:]


def _flush_root_journal() -> None:
    """Write the ext4 journal of the running root filesystem back to its home blocks.

    sync only commits changes to the journal; ext4 may leave them there for a long
    time. A raw copy that reads home blocks and journal at different moments can
    then lose recent changes (e.g. a KIBM update) when e2fsck repairs the image.
    Freezing flushes the journal; the root filesystem is thawed right away.
    """
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            fcntl.ioctl(root_fd, FIFREEZE, 0)
        except OSError as exc:
            raise OSError(f"Das Journal der System-microSD konnte nicht abgeschlossen werden ({exc.strerror}).") from exc
        fcntl.ioctl(root_fd, FITHAW, 0)
    finally:
        os.close(root_fd)


def _repair_image(path: Path) -> bool:
    """Replay the journal and repair each Linux filesystem inside the image.

    A live card is read while the system writes, so the captured ext4 has an
    open journal and inconsistent bitmaps. Returns True if e2fsck changed the image.
    """
    with path.open("rb") as image:
        mbr = image.read(partitions.SECTOR_BYTES)
    changed = False
    for offset, length in partitions.linux_partitions(mbr):
        loop = _run([
            "losetup", "--find", "--show", "--offset", str(offset), "--sizelimit", str(length), "--", str(path),
        ]).stdout.strip()
        try:
            result = _run_cancellable(["e2fsck", "-fy", loop], timeout=E2FSCK_TIMEOUT_SECONDS)
        finally:
            _run(["losetup", "--detach", loop])
        if result.returncode >= 4:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
            raise OSError(f"Das Dateisystem im Image ließ sich nicht reparieren (e2fsck {result.returncode}). {detail[0]}".strip())
        changed = changed or result.returncode in (1, 2)
    return changed


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


def _existing_images(volumes: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    images = []
    for volume in _image_volumes() if volumes is None else volumes:
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
                            "location": "usb",
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
    _run(["mount", "-t", "exfat", "-o", "rw,nosuid,nodev,noexec", partition, str(VOLUME_PATH)])


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


def _invalidate_images() -> None:
    global _images_generation
    with _lock:
        _images_generation += 1


def _set_job(**values: Any) -> None:
    if "state" in values:
        _invalidate_images()
    with _lock:
        if _job:
            for key, value in values.items():
                setattr(_job, key, value)
            # Never blocks: a background thread sends it to Moonraker.
            PRINTER_STATUS.update(_job_data(_job))


def _copy_for_shrink(source: Path, target: Path) -> None:
    copied = 0
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(CHUNK_BYTES):
            writer.write(chunk)
            copied += len(chunk)
            _progress(shrink_bytes=copied)


def _shrink_image(job: MediaJob, full_path: Path, final_path: Path) -> dict[str, Any]:
    working_path = full_path.parent / f".{job.job_id}.img.part"
    _progress(
        state="schrumpfen", phase="kopie", shrink_total_bytes=job.image_size,
        message="Arbeitskopie für PiShrink wird erstellt.",
    )
    try:
        _copy_for_shrink(full_path, working_path)
        _progress(phase="verkleinern", message="PiShrink prüft und verkleinert Dateisystem und Partition.")
        # PiShrink leaves only a few MB free, less than ext4 reserves for root, so the
        # restored card must grow its root partition on first boot (PiShrink autoexpand).
        try:
            result = _run_cancellable([str(PISHRINK_PATH), "-n", str(working_path)], timeout=60 * 60 * 4, check=True)
        except JobCancelled:
            _detach_loops(working_path)
            raise
        if "autoexpand will not be enabled" in result.stdout or "does not yet support autoexpanding" in result.stdout:
            raise OSError("PiShrink konnte die automatische Vergrößerung beim ersten Start nicht einrichten.")
        final_size = working_path.stat().st_size
        if not 0 < final_size < job.image_size:
            raise OSError("PiShrink hat kein kleineres, gültiges Image erzeugt.")
        _progress(
            phase="pruefsumme", shrink_bytes=0, shrink_total_bytes=final_size,
            message="Prüfsumme des verkleinerten Images wird berechnet.",
        )
        final_hash = _hash_file(working_path, lambda count: _progress(shrink_bytes=count))
        with working_path.open("rb") as image:
            os.fsync(image.fileno())
        working_path.rename(final_path)
    finally:
        working_path.unlink(missing_ok=True)
    return {"filename": final_path.name, "size_bytes": final_size, "sha256": final_hash, "kind": "geshrinkt"}


def _nas_identity(config: Any) -> list[str]:
    return ["nas", config.server, config.share, config.folder]


def _saved_nas_for(image: dict[str, Any]) -> Any:
    config = nas.load_config(NAS_CONFIG_PATH)
    if config is None or _nas_identity(config) != image["volume"]["identity"]:
        raise ValueError("Die NAS-Verbindung wurde geändert. Lade die Liste neu.")
    return config


def _listed_source(directory: Path, image: dict[str, Any]) -> Path:
    source = directory / image["filename"]
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or _image_id(image["volume"], source, info) != image["id"]:
        raise ValueError("Die Image-Datei wurde seit der Auswahl verändert. Wähle sie erneut aus.")
    return source


def _shrink_listed_image(job: MediaJob, image: dict[str, Any], directory: Path) -> dict[str, Any]:
    source = _listed_source(directory, image)
    info = source.lstat()
    shrinkability = _classify_image(source, info.st_size, info.st_mtime_ns, use_cache=False)
    if shrinkability["status"] != "shrinkable":
        raise ValueError("Das Image ist bereits kompakt oder sein Dateisystem lässt sich nicht sicher prüfen.")
    filesystem = os.statvfs(directory)
    if filesystem.f_bavail * filesystem.f_frsize < job.image_size:
        where = "dem NAS" if job.target == "nas" else "dem Stick"
        raise ValueError(f"Auf {where} fehlt freier Speicherplatz für eine vollständige Arbeitskopie.")
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-")[:100] or "image"
    return _shrink_image(job, source, directory / f"{base}-{job.job_id}-geshrinkt.img")


def _run_existing_nas_shrink(job: MediaJob, image: dict[str, Any]) -> None:
    try:
        if _printer_active():
            raise ValueError("Moonraker meldet einen aktiven Druckauftrag.")
        config = _saved_nas_for(image)
        with _nas_lock, _nas_mounted(config, NAS_CREDENTIALS_PATH) as root:
            result = _shrink_listed_image(job, image, nas.safe_subpath(root, config.folder))
            _set_job(state="bereit", phase="fertig", message="Verkleinerte Kopie ist auf dem NAS gespeichert.", files=[result])
    except Exception as exc:
        with _lock:
            finished = job.state == "bereit"
        if not finished:
            _end_failed(exc, "NAS-Image konnte nicht verkleinert werden.", SHRINK_CANCELLED)


def _run_existing_shrink(job: MediaJob, image: dict[str, Any]) -> None:
    if image["volume"].get("kind") == "nas":
        _run_existing_nas_shrink(job, image)
        return
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
        result = _shrink_listed_image(job, image, VOLUME_PATH)
        _set_job(state="bereit", phase="fertig", message="Verkleinerte Kopie ist auf USB gespeichert.", files=[result])
    except (OSError, ValueError, subprocess.SubprocessError, JobCancelled) as exc:
        _end_failed(exc, "USB-Image konnte nicht verkleinert werden.", SHRINK_CANCELLED)


TARGET_TEXT = {
    "usb": {
        "writing": "Vollständiges Karten-Image wird auf den USB-Stick geschrieben.",
        "verifying": "Geschriebenes Image wird vom USB-Stick zurückgelesen und geprüft.",
        "verify_failed": "Der SHA-256-Vergleich der geschriebenen USB-Image-Datei ist fehlgeschlagen.",
        "done": "Image ist auf dem USB-Stick gespeichert und kann heruntergeladen werden.",
        "failed": "USB-Image konnte nicht sicher erstellt werden.",
    },
    "nas": {
        "writing": "Vollständiges Karten-Image wird auf das NAS geschrieben.",
        "verifying": "Geschriebenes Image wird vom NAS zurückgelesen und geprüft.",
        "verify_failed": "Der SHA-256-Vergleich der geschriebenen NAS-Image-Datei ist fehlgeschlagen.",
        "done": "Image ist auf dem NAS gespeichert.",
        "failed": "NAS-Image konnte nicht sicher erstellt werden.",
    },
}


BACKUP_CANCELLED = "Sicherung wurde abgebrochen. Unvollständige Dateien wurden gelöscht."
SHRINK_CANCELLED = "Verkleinern wurde abgebrochen. Die Arbeitskopie wurde gelöscht, das Original ist unverändert."


SOURCE_READ_ERROR = (
    "Die System-microSD meldet bei {position} GB einen Lesefehler. Die Karte ist wahrscheinlich defekt: "
    "Starte keine weitere Sicherung und ersetze die Karte bald. Wie du die Daten rettest, steht in der README "
    "unter „Die System-microSD meldet Lesefehler“."
)
# Read errors of the system card are translated where the card is read, so an
# I/O error that arrives here came from the backup target.
TARGET_IO_ERROR = (
    "Ein-/Ausgabefehler beim Zugriff auf das Sicherungsziel. Prüfe die Verbindung zum NAS bzw. den USB-Stick. "
    "Meldet die Statusleiste oben zusätzlich Fehler der System-microSD, liegt es an der Karte."
)


def _end_failed(exc: BaseException, failed: str, cancelled: str, files: Optional[list[dict[str, Any]]] = None) -> None:
    if isinstance(exc, JobCancelled):
        _set_job(state="abgebrochen", phase="abgebrochen", message=cancelled, error=None, files=files or [])
    else:
        error = TARGET_IO_ERROR if isinstance(exc, OSError) and exc.errno == errno.EIO else str(exc)
        _set_job(state="fehler", phase="fehler", message=failed, error=error, files=files or [])


_CARD_DEVICE = re.compile(r"\bmmc(?:blk)?0")
_CARD_FAILURE = re.compile(r"\berror\b|timeout|timed out", re.IGNORECASE)


def _sd_health() -> dict[str, Any]:
    """Errors of the system card from the kernel log since boot and the ext4 error counters."""
    kernel_checked = False
    kernel_errors: list[str] = []
    try:
        log = _run(["journalctl", "--dmesg", "--boot", "--no-pager", "--quiet", "--output=cat"], timeout=20).stdout
        kernel_checked = True
        kernel_errors = [
            line.strip() for line in log.splitlines()
            if _CARD_DEVICE.search(line) and _CARD_FAILURE.search(line)
        ]
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Kernel log not readable: %s", exc)
    filesystem_errors = 0
    for counter in EXT4_SYSFS.glob("mmcblk0p*/errors_count"):
        try:
            filesystem_errors += int(counter.read_text().strip() or 0)
        except (OSError, ValueError):
            continue
    return {
        "kernel_checked": kernel_checked,
        "kernel_errors": len(kernel_errors),
        "last_kernel_error": kernel_errors[-1][:300] if kernel_errors else "",
        "filesystem_errors": filesystem_errors,
    }


def _fail_job(job: MediaJob, exc: BaseException) -> None:
    _end_failed(exc, TARGET_TEXT[job.target]["failed"], BACKUP_CANCELLED)


def _run_job(job: MediaJob, device: dict[str, Any], hostname: str) -> None:
    try:
        if _printer_active():
            raise ValueError("Moonraker meldet einen aktiven Druck. Es wurde noch nichts formatiert.")
        current_device = _selected_device(device["path"])
        if current_device["major_minor"] != device["major_minor"] or current_device["size_bytes"] != device["size_bytes"]:
            raise ValueError("Der USB-Stick wurde seit der Auswahl gewechselt. Es wurde nichts formatiert.")
        if _disk_size(SOURCE_DEVICE) != job.device_size:
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
        _check_cancel()
    except Exception as exc:
        _fail_job(job, exc)
        return
    _capture_image(job, VOLUME_PATH, hostname)


def _run_nas_job(job: MediaJob, config: Any, hostname: str) -> None:
    try:
        with _nas_lock, _nas_mounted(config, NAS_CREDENTIALS_PATH) as root:
            directory = _nas_folder(root, config.folder)
            filesystem = os.statvfs(directory)
            required = _usb_image_bytes(job.image_size, job.shrink)
            if filesystem.f_bavail * filesystem.f_frsize < required:
                raise ValueError(
                    f"Auf dem NAS ist nicht genug freier Platz. Diese Auswahl benötigt rund {required / 1024**3:.2f} GiB."
                )
            if _disk_size(SOURCE_DEVICE) != job.device_size:
                raise ValueError("Die Größe der System-microSD hat sich geändert.")
            if _printer_active():
                raise ValueError("Moonraker meldet einen aktiven Druck. Die Sicherung wurde nicht gestartet.")
            _check_cancel()
            _capture_image(job, directory, hostname)
    except Exception as exc:
        # _capture_image reports its own result; only failures around it (mounting,
        # folder, space, unmounting before the image was finished) end up here.
        with _lock:
            finished = job.state in {"bereit", "fehler", "abgebrochen"}
        if finished:
            logger.warning("NAS job %s: %s", job.job_id, exc)
        else:
            _fail_job(job, exc)


def _capture_image(job: MediaJob, directory: Path, hostname: str) -> None:
    text = TARGET_TEXT[job.target]
    full_path: Optional[Path] = None
    part_path: Optional[Path] = None
    full_image_ready = False
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_hostname = re.sub(r"[^A-Za-z0-9._-]+", "-", hostname).strip(".-") or "klipper"
        base = f"{safe_hostname}-{stamp}-sdkarte"
        full_path = directory / (f"{base}-voll.img" if job.shrink else f"{base}.img")
        # Hidden until complete and repaired: an aborted job, a pulled stick or a
        # power loss never leaves a file that looks like a usable image.
        part_path = directory / f".{full_path.name}.part"
        _set_job(state="sichern", phase="sichern", message=text["writing"], sent_bytes=0)
        digest = hashlib.sha256()
        next_printer_check = time.monotonic()
        # Write pending data of the running system to the card before reading it.
        os.sync()
        _flush_root_journal()
        source_fd = os.open(SOURCE_DEVICE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            if not stat.S_ISBLK(os.fstat(source_fd).st_mode):
                raise OSError("Die System-microSD ist kein Blockgerät.")
            # The whole-card device has its own page cache, which writes to the mounted
            # partition never update. Blocks cached by an earlier backup (e.g. the
            # journal superblock) would otherwise be copied stale.
            os.posix_fadvise(source_fd, 0, 0, os.POSIX_FADV_DONTNEED)
            with part_path.open("xb", buffering=0) as output:
                copied = 0
                while copied < job.image_size:
                    _check_cancel()
                    if time.monotonic() >= next_printer_check:
                        if _printer_active():
                            raise OSError("Moonraker meldet einen aktiven Druck. Image-Datei ist unvollständig.")
                        next_printer_check = time.monotonic() + PRINTER_RECHECK_SECONDS
                    try:
                        chunk = os.read(source_fd, min(CHUNK_BYTES, job.image_size - copied))
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            raise OSError(SOURCE_READ_ERROR.format(position=f"{copied / 1e9:.1f}".replace(".", ","))) from exc
                        raise
                    if not chunk:
                        raise OSError(f"Die microSD lieferte nur {copied} von {job.image_size} Bytes.")
                    _write_all(output, chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    _set_job(sent_bytes=copied)
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.close(source_fd)
        if job.verify_image:
            _progress(state="verifizieren", phase="verifizieren", message=text["verifying"])
            if _hash_file(part_path, lambda count: _progress(verified_bytes=count)) != digest.hexdigest():
                raise OSError(text["verify_failed"])

        _progress(
            state="reparieren", phase="dateisystem", verified_bytes=0,
            message="Journal wird abgeschlossen und das Dateisystem im Image geprüft und repariert.",
        )
        final_hash = digest.hexdigest()
        if _repair_image(part_path):
            _progress(phase="pruefsumme", message="Prüfsumme des reparierten Images wird berechnet.")
            final_hash = _hash_file(part_path, lambda count: _progress(verified_bytes=count))
        with part_path.open("rb") as image:
            os.fsync(image.fileno())
        part_path.rename(full_path)
        part_path = None
        _set_job(sha256=final_hash)
        full_image_ready = True

        if job.shrink:
            final_path = directory / f"{base}-geshrinkt.img"
            files = [_shrink_image(job, full_path, final_path)]
            if job.keep_full:
                files.insert(0, {"filename": full_path.name, "size_bytes": full_path.stat().st_size, "sha256": final_hash, "kind": "vollständig"})
            else:
                full_path.unlink()
        else:
            files = [{"filename": full_path.name, "size_bytes": full_path.stat().st_size, "sha256": final_hash, "kind": "vollständig"}]
        _set_job(state="bereit", phase="fertig", message=text["done"], files=files)
    except Exception as exc:
        if part_path is not None:
            try:
                part_path.unlink(missing_ok=True)
            except OSError as cleanup:
                logger.warning("Could not remove %s: %s", part_path, cleanup)
        files = []
        if full_image_ready and full_path and full_path.is_file() and full_path.stat().st_size == job.image_size:
            files.append({
                "filename": full_path.name, "size_bytes": full_path.stat().st_size,
                "sha256": job.sha256, "kind": "vollständig",
            })
        cancelled = "Sicherung wurde abgebrochen. Das vollständige Image ist erhalten." if files else BACKUP_CANCELLED
        _end_failed(exc, text["failed"], cancelled, files)


def _job_data(job: MediaJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id, "state": job.state, "phase": job.phase, "message": job.message,
        "image_size_bytes": job.image_size, "sent_bytes": job.sent_bytes, "verified_bytes": job.verified_bytes,
        "shrink_bytes": job.shrink_bytes, "shrink_total_bytes": job.shrink_total_bytes,
        "sha256": job.sha256, "error": job.error, "files": job.files,
        "shrink": job.shrink, "keep_full": job.keep_full, "verify_image": job.verify_image,
        "target": job.target, "cancel_requested": job.cancel_requested,
    }


def _replace_listed(kind: str, images: list[dict[str, Any]]) -> None:
    """Replace the selectable images of one location; caller holds _lock."""
    for image_id in [key for key, item in _listed_images.items() if item["volume"].get("kind", "usb") == kind]:
        del _listed_images[image_id]
    _listed_images.update((item["id"], {**item, "volume": {**item["volume"], "kind": kind}}) for item in images)


def _is_mounted(path: Path) -> bool:
    # Read the mount table instead of stat-ing the mount point: on a CIFS mount whose
    # NAS vanished, os.path.ismount() fails and would report "not mounted".
    target = str(path)
    try:
        with MOUNTINFO_PATH.open(encoding="utf-8") as mountinfo:
            return any(len(fields) > 4 and fields[4] == target for fields in map(str.split, mountinfo))
    except OSError:
        return os.path.ismount(target)


def _nas_unmount() -> None:
    if not _is_mounted(NAS_MOUNT_PATH):
        return
    try:
        _run(["umount", "--", str(NAS_MOUNT_PATH)], timeout=30)
    except subprocess.SubprocessError:
        # A vanished NAS can block a normal unmount; detach it so the next action can mount again.
        _run(["umount", "--lazy", "--", str(NAS_MOUNT_PATH)], timeout=15)


@contextmanager
def _nas_mounted(config: Any, credentials: Path) -> Iterator[Path]:
    """Mount the share only for one action, in the worker's own mount namespace."""
    NAS_MOUNT_PATH.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_mounted(NAS_MOUNT_PATH):
        # Callers hold _nas_lock, so a mount found here is left over from an earlier
        # action whose unmount failed (e.g. the NAS was switched off).
        _nas_unmount()
        if _is_mounted(NAS_MOUNT_PATH):
            raise ValueError("Der NAS-Einhängepunkt ist bereits belegt.")
    try:
        _run(nas.mount_args(config, credentials, NAS_MOUNT_PATH), timeout=nas.MOUNT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _nas_unmount()
        raise ValueError(nas.mount_error("", timed_out=True)) from None
    except subprocess.CalledProcessError as exc:
        # Translate here: the generic handler would append raw mount output.
        raise ValueError(nas.mount_error(exc.stderr or exc.stdout or "", timed_out=False)) from None
    try:
        yield NAS_MOUNT_PATH
    except BaseException:
        # Keep the original error; an unmount failure on a dead NAS would only hide it.
        try:
            _nas_unmount()
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("NAS unmount failed: %s", exc)
        raise
    _nas_unmount()


def _request_text(request: dict[str, Any], key: str) -> str:
    value = request.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"Ungültige Eingabe: {key}")
    return value


@contextmanager
def _nas_credentials(request: dict[str, Any], *, listing: bool = False) -> Iterator[tuple[Any, Path]]:
    """Config and credentials file for a request; a temporary file for unsaved passwords."""
    saved = nas.load_config(NAS_CONFIG_PATH)
    if not _request_text(request, "server"):
        if saved is None:
            raise ValueError("Keine NAS-Verbindung gespeichert.")
        yield saved, NAS_CREDENTIALS_PATH
        return
    share = _request_text(request, "share") or ("IPC$" if listing else "")
    config = nas.validate_config(
        _request_text(request, "server"), share, _request_text(request, "username"), _request_text(request, "folder"),
    )
    password = nas.validate_password(_request_text(request, "password"))
    if not password:
        same_target = saved is not None and (config.server, config.username, config.domain) == (
            saved.server, saved.username, saved.domain) and (listing or config.share == saved.share)
        if not same_target:
            # The stored password must never be sent to a different host, share or account.
            raise ValueError("Bitte das Passwort eingeben.")
        yield config, NAS_CREDENTIALS_PATH
        return
    RUN_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    path = RUN_DIR / f"nas-cred-{secrets.token_hex(8)}"
    nas.write_private(path, nas.credentials_text(config, password))
    try:
        yield config, path
    finally:
        path.unlink(missing_ok=True)


def _nas_job_busy() -> bool:
    with _lock:
        return bool(_job and _job.target == "nas" and _job.state in BUSY_STATES)


@contextmanager
def _nas_exclusive() -> Iterator[None]:
    if _nas_job_busy():
        raise ValueError("Während einer laufenden NAS-Sicherung ist das nicht möglich.")
    if not _nas_lock.acquire(timeout=NAS_LOCK_WAIT_SECONDS):
        raise ValueError("Das NAS wird gerade von einem anderen Vorgang benutzt. Versuche es gleich noch einmal.")
    try:
        yield
    finally:
        _nas_lock.release()


@contextmanager
def _nas_session(request: dict[str, Any]) -> Iterator[tuple[Any, Path]]:
    with _nas_exclusive(), _nas_credentials(request) as (config, credentials), _nas_mounted(config, credentials) as root:
        yield config, root


def _nas_folder(root: Path, folder: str) -> Path:
    """The backup folder on the mounted share; must exist and be writable."""
    directory = nas.safe_subpath(root, folder)
    if not directory.is_dir():
        raise ValueError(f"Sicherungsordner nicht gefunden: {folder or '/'}")
    # A fresh name with O_EXCL|O_NOFOLLOW never follows or overwrites anything on the share.
    probe = directory / f".kibm-write-test-{secrets.token_hex(4)}"
    try:
        os.close(os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600))
        probe.unlink()
    except OSError:
        raise ValueError("Keine Schreibrechte im Sicherungsordner.") from None
    return directory


def _avahi_output() -> str:
    try:
        return _run(["avahi-browse", "-rpt", "_smb._tcp"], timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _own_ipv4() -> str:
    # No packet is sent; the kernel only picks the outgoing address. Works without
    # netlink, which the worker sandbox does not allow.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]


def _port_open(host: str) -> bool:
    try:
        with socket.create_connection((host, 445), timeout=0.4):
            return True
    except OSError:
        return False


def _reverse_name(ip: str) -> Optional[str]:
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        name = pool.submit(socket.gethostbyaddr, ip).result(timeout=1)[0]
        return name if name != ip else None
    except Exception:
        return None
    finally:
        pool.shutdown(wait=False)


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
            if not isinstance(request, dict):
                self._reply({"error": "invalid_request"})
                return
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
            elif action == "cancel":
                self._cancel(request)
            elif action == "sd_health":
                self._reply(_sd_health())
            elif action == "current":
                with _lock:
                    self._reply({"job": _job_data(_job) if _job else None})
            elif action == "download":
                self._download(request)
            elif action == "eject":
                self._eject(request)
            elif action == "update_status":
                self._update_status()
            elif action == "update_start":
                self._update_start(request)
            elif action == "nas_start":
                self._nas_start(request)
            elif action == "nas_images":
                self._nas_images(request)
            elif action == "nas_get":
                self._nas_get()
            elif action == "nas_check":
                self._nas_check(request)
            elif action == "nas_browse":
                self._nas_browse(request)
            elif action == "nas_mkdir":
                self._nas_mkdir(request)
            elif action == "nas_save":
                self._nas_save(request)
            elif action == "nas_delete":
                self._nas_delete(request)
            elif action == "nas_shares":
                self._nas_shares(request)
            elif action == "nas_discover":
                self._nas_discover()
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
        global _images_cache
        with _scan_lock:
            with _lock:
                if _job and _job.state in BUSY_STATES:
                    self._reply({"images": []})
                    return
                generation = _images_generation
            volumes = _image_volumes()
            key = (tuple(sorted(map(json.dumps, (volume["identity"] for volume in volumes)))), generation)
            if _images_cache and _images_cache[0] == key:
                images = _images_cache[1]
            else:
                # Mounting and reading the stick is slow; only rescan when a
                # stick changed or KIBM wrote to it since the last scan.
                images = _existing_images(volumes)
                _images_cache = (key, images)
            with _lock:
                _replace_listed("usb", images)
            self._reply({"images": [
                {key: value for key, value in item.items() if key != "volume"} for item in images
            ]})

    def _nas_images(self, request: dict[str, Any]) -> None:
        if _nas_job_busy():
            self._reply({"images": [], "busy": True})
            return
        images = []
        with _nas_session({}) as (config, root):
            directory = nas.safe_subpath(root, config.folder)
            if not directory.is_dir():
                raise ValueError(f"Sicherungsordner nicht gefunden: {config.folder or '/'}")
            volume = {"kind": "nas", "identity": _nas_identity(config)}
            for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
                if path.suffix.lower() != ".img" or path.name.startswith("."):
                    continue
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_size > 0:
                    images.append({
                        "id": _image_id(volume, path, info), "filename": path.name, "size_bytes": info.st_size,
                        "shrinkability": _classify_image(path, info.st_size, info.st_mtime_ns),
                        "location": "nas", "volume": volume,
                    })
                if len(images) >= 100:
                    break
        with _lock:
            _replace_listed("nas", images)
        self._reply({
            "images": [{key: value for key, value in item.items() if key != "volume"} for item in images],
            "display": config.display(),
        })

    def _start_existing(self, request: dict[str, Any]) -> None:
        global _job
        _ensure_update_idle()
        with _scan_lock, _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Auf dem USB-Stick läuft bereits ein Auftrag oder Download.")
            image = _listed_images.get(request.get("image_id"))
            if not image:
                raise ValueError("Die Image-Datei ist nicht mehr verfügbar. Wähle sie erneut aus.")
            if image.get("shrinkability", {}).get("status") != "shrinkable":
                raise ValueError("Dieses Image ist bereits kompakt oder kann nicht sicher geprüft werden.")
            volume = image["volume"]
            on_nas = volume.get("kind") == "nas"
            job = MediaJob(
                job_id=secrets.token_hex(10), state="wird vorbereitet", phase="vorbereitung",
                message="Image auf dem NAS wird geöffnet." if on_nas else "Image auf USB wird geöffnet.",
                image_size=image["size_bytes"], source_device="nas" if on_nas else volume["path"],
                shrink=True, keep_full=True, verify_image=False, target="nas" if on_nas else "usb",
            )
            _job = job
        threading.Thread(target=_run_existing_shrink, args=(job, image), daemon=True).start()
        device = {"path": image["filename"], "model": "NAS (SMB)", "size_bytes": 0} if on_nas else volume["device"]
        self._reply({"job": _job_data(job), "device": device})

    def _delete_nas_image(self, image: dict[str, Any]) -> None:
        filename = image["filename"]
        config = _saved_nas_for(image)
        with _nas_exclusive(), _nas_mounted(config, NAS_CREDENTIALS_PATH) as root:
            directory = nas.safe_subpath(root, config.folder)
            _listed_source(directory, image).unlink()
        _invalidate_images()
        with _lock:
            _listed_images.pop(image["id"], None)
        self._reply({"deleted": filename})

    def _delete_image(self, request: dict[str, Any]) -> None:
        _ensure_update_idle()
        with _lock:
            image = _listed_images.get(request.get("image_id"))
        if image and image["volume"].get("kind") == "nas":
            # Mounting a share can take seconds: do it without holding _lock, which status polls need.
            filename = image.get("filename")
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.lower().endswith(".img"):
                raise ValueError("Es kann nur eine einzelne .img-Datei gelöscht werden.")
            self._delete_nas_image(image)
            return
        with _scan_lock, _lock:
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
            _invalidate_images()
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
        device_size = _disk_size(SOURCE_DEVICE)
        if device_size <= 0:
            raise ValueError("Größe der System-microSD nicht verfügbar.")
        image_size = _capture_size(SOURCE_DEVICE, device_size)
        shrink = request.get("shrink") is True
        keep_full = shrink and request.get("keep_full") is True
        _check_usb_capacity(device, image_size, shrink)
        job = MediaJob(
            job_id=secrets.token_urlsafe(20),
            state="wird vorbereitet", phase="vorbereitung", message="USB-Ziel wird geprüft.",
            image_size=image_size, device_size=device_size, source_device=SOURCE_DEVICE, shrink=shrink, keep_full=keep_full,
            verify_image=request.get("verify_image", True) is not False,
        )
        with _scan_lock, _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Auf dem USB-Stick läuft bereits ein Auftrag oder Download.")
            _job = job
        _invalidate_images()
        threading.Thread(target=_run_job, args=(job, device, socket.gethostname()), daemon=True).start()
        self._reply({"job": _job_data(job), "device": device})

    def _nas_start(self, request: dict[str, Any]) -> None:
        global _job
        _ensure_update_idle()
        if request.get("confirmed") is not True:
            raise ValueError("Bestätige zuerst den sicheren Druckerstatus.")
        config = nas.load_config(NAS_CONFIG_PATH)
        if config is None:
            raise ValueError("Keine NAS-Verbindung gespeichert.")
        device_size = _disk_size(SOURCE_DEVICE)
        if device_size <= 0:
            raise ValueError("Größe der System-microSD nicht verfügbar.")
        shrink = request.get("shrink") is True
        job = MediaJob(
            job_id=secrets.token_urlsafe(20),
            state="wird vorbereitet", phase="vorbereitung", message="NAS-Verbindung wird geprüft.",
            image_size=_capture_size(SOURCE_DEVICE, device_size), device_size=device_size, source_device=SOURCE_DEVICE,
            shrink=shrink, keep_full=shrink and request.get("keep_full") is True,
            verify_image=request.get("verify_image", True) is not False, target="nas",
        )
        with _scan_lock, _lock:
            if _job and (_job.state in BUSY_STATES or _job.active_downloads):
                raise ValueError("Es läuft bereits ein Image-Auftrag oder Download.")
            _job = job
        threading.Thread(target=_run_nas_job, args=(job, config, socket.gethostname()), daemon=True).start()
        self._reply({"job": _job_data(job), "device": {"path": config.display(), "model": "NAS (SMB)", "size_bytes": 0}})

    def _status(self, request: dict[str, Any]) -> None:
        with _lock:
            job = _job
            if not job or job.job_id != request.get("job_id"):
                raise ValueError("USB-Auftrag nicht gefunden.")
            self._reply({"job": _job_data(job)})

    def _cancel(self, request: dict[str, Any]) -> None:
        with _lock:
            job = _job
            if not job or job.job_id != request.get("job_id"):
                raise ValueError("Auftrag nicht gefunden.")
            if job.state not in BUSY_STATES:
                raise ValueError("Der Auftrag läuft nicht mehr.")
            job.cancel_requested = True
            self._reply({"job": _job_data(job)})

    def _download(self, request: dict[str, Any]) -> None:
        _ensure_update_idle()
        name = request.get("filename")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Ungültiger Dateiname.")
        with _lock:
            job = _job
            file_info = next((item for item in job.files if item["filename"] == name), None) if job else None
            if job and job.target == "nas":
                raise ValueError("NAS-Images liegen bereits auf dem NAS.")
            if not job or job.state not in {"bereit", "fehler", "abgebrochen"} or not file_info:
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
        with _scan_lock, _lock:
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

    def _nas_get(self) -> None:
        config = nas.load_config(NAS_CONFIG_PATH)
        if config is None:
            self._reply({"configured": False})
            return
        self._reply({
            "configured": True, "server": config.server, "share": config.share, "username": config.user_label(),
            "folder": config.folder, "display": config.display(),
        })

    def _nas_check(self, request: dict[str, Any]) -> None:
        config = nas.load_config(NAS_CONFIG_PATH)
        if config is None:
            self._reply({"ok": False, "error": "Keine NAS-Verbindung gespeichert."})
            return
        if _nas_job_busy():
            self._reply({"ok": True, "busy": True, "display": config.display()})
            return
        try:
            with _nas_session({}) as (config, root):
                filesystem = os.statvfs(_nas_folder(root, config.folder))
        except ValueError as exc:
            self._reply({"ok": False, "error": str(exc)})
            return
        self._reply({"ok": True, "display": config.display(), "free_bytes": filesystem.f_bavail * filesystem.f_frsize})

    def _nas_browse(self, request: dict[str, Any]) -> None:
        path = nas.validate_folder(_request_text(request, "path"))
        with _nas_session(request) as (_config, root):
            directory = nas.safe_subpath(root, path)
            if not directory.is_dir():
                raise ValueError(f"Ordner nicht gefunden: {path or '/'}")
            with os.scandir(directory) as entries:
                folders = sorted(
                    (entry.name for entry in entries
                     if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False)),
                    key=str.lower,
                )[:200]
        self._reply({"path": path, "folders": folders})

    def _nas_mkdir(self, request: dict[str, Any]) -> None:
        path = nas.validate_folder(_request_text(request, "path"))
        name = nas.validate_segment(_request_text(request, "name"))
        with _nas_session(request) as (_config, root):
            parent = nas.safe_subpath(root, path)
            if not parent.is_dir():
                raise ValueError(f"Ordner nicht gefunden: {path or '/'}")
            try:
                (parent / name).mkdir(mode=0o700)
            except FileExistsError:
                raise ValueError("Ein Ordner mit diesem Namen existiert bereits.") from None
        self._reply({"path": f"{path}/{name}" if path else name})

    def _nas_save(self, request: dict[str, Any]) -> None:
        if not _request_text(request, "server"):
            raise ValueError("Bitte IP-Adresse oder Name des NAS angeben.")
        password = _request_text(request, "password")
        with _nas_session(request) as (config, root):
            _nas_folder(root, config.folder)
            NAS_STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            if password:
                nas.write_private(NAS_CREDENTIALS_PATH, nas.credentials_text(config, password))
            nas.save_config(NAS_CONFIG_PATH, config)
        self._reply({"saved": True, "display": config.display()})

    def _nas_delete(self, request: dict[str, Any]) -> None:
        with _nas_exclusive():
            NAS_CONFIG_PATH.unlink(missing_ok=True)
            NAS_CREDENTIALS_PATH.unlink(missing_ok=True)
        self._reply({"deleted": True})

    def _nas_shares(self, request: dict[str, Any]) -> None:
        with _nas_credentials(request, listing=True) as (config, credentials):
            try:
                output = _run(["smbclient", "-g", "-L", f"//{config.server}", "-A", str(credentials)], timeout=15).stdout
            except FileNotFoundError:
                raise ValueError("Die Freigabenliste ist auf diesem System nicht verfügbar (smbclient fehlt). Gib den Freigabenamen von Hand ein.") from None
            except subprocess.TimeoutExpired:
                raise ValueError(nas.mount_error("", timed_out=True)) from None
            except subprocess.CalledProcessError as exc:
                detail = f"{exc.stdout or ''}\n{exc.stderr or ''}"
                if "NT_STATUS_LOGON_FAILURE" in detail or "NT_STATUS_ACCESS_DENIED" in detail:
                    raise ValueError(nas.mount_error("error(13)", timed_out=False)) from None
                if any(code in detail for code in ("NT_STATUS_HOST_UNREACHABLE", "NT_STATUS_IO_TIMEOUT", "NT_STATUS_CONNECTION_REFUSED")):
                    raise ValueError(nas.mount_error("error(112)", timed_out=False)) from None
                raise ValueError("Die Freigaben des NAS konnten nicht abgefragt werden.") from None
        self._reply({"shares": nas.parse_shares(output)})

    def _nas_discover(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            avahi = pool.submit(_avahi_output)
            scanned = pool.submit(lambda: nas.scan_port_445(nas.subnet_hosts(_own_ipv4()), _port_open))
            found = scanned.result()
            mdns = nas.parse_avahi(avahi.result())
        self._reply({"devices": nas.merge_discovered(mdns, found, _reverse_name)})

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
