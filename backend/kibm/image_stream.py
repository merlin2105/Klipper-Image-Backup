from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import re
import secrets
import socket
import stat
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

from . import partitions, services

BLOCKGETSIZE64 = 0x80081272
CHUNK_BYTES = 4 * 1024 * 1024
PRINTER_RECHECK_SECONDS = 10
_lock = threading.Lock()
_jobs: dict[str, "ImageJob"] = {}
JOB_TTL_SECONDS = 60 * 60
UNCLAIMED_TTL_SECONDS = 5 * 60
MAX_RETAINED_JOBS = 10
EXPIRED_MESSAGE = "Der Download wurde nicht rechtzeitig gestartet. Bitte das Image erneut anfordern."


@dataclass
class ImageJob:
    job_id: str
    session_id: str
    download_token: str
    source_path: str
    source_device: int
    # size_bytes is what gets imaged: up to the end of the last partition.
    size_bytes: int
    device_bytes: int
    filename: str
    created_at: float = field(default_factory=time.time)
    state: str = "bereit"
    sent_bytes: int = 0
    sha256: Optional[str] = None
    error: Optional[str] = None
    download_claimed: bool = False


def _expire_unclaimed_jobs() -> None:
    # Caller holds _lock. A job whose download never started must not block
    # new images or updates for the whole job lifetime.
    cutoff = time.time() - UNCLAIMED_TTL_SECONDS
    for job in _jobs.values():
        if job.state == "bereit" and job.created_at < cutoff:
            job.state = "abgelaufen"
            job.error = EXPIRED_MESSAGE


def has_active_job() -> bool:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _lock:
        _expire_unclaimed_jobs()
        return any(
            job.created_at >= cutoff and job.state in {"bereit", "streaming"}
            for job in _jobs.values()
        )


def _device_size(fd: int) -> int:
    value = bytearray(8)
    fcntl.ioctl(fd, BLOCKGETSIZE64, value, True)
    return struct.unpack("Q", value)[0]


def create_job(session_id: str, device: dict) -> dict:
    path = device.get("path")
    if path != "/dev/mmcblk0" or device.get("type") != "disk":
        raise ValueError("Unterstützt wird derzeit nur die interne microSD unter /dev/mmcblk0.")
    if not isinstance(device.get("size_bytes"), int) or device["size_bytes"] <= 0:
        raise ValueError("Die Größe der System-microSD konnte nicht sicher ermittelt werden.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError("KIBM darf die System-microSD nicht lesend öffnen.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode):
            raise ValueError("Das erkannte Systemlaufwerk ist kein Blockgerät.")
        actual_size = _device_size(fd)
        if actual_size != device["size_bytes"]:
            raise ValueError("Die erkannte SD-Kartengröße hat sich geändert; bitte Diagnose erneut laden.")
        capture_size = partitions.capture_bytes(os.pread(fd, partitions.SECTOR_BYTES, 0), actual_size)
        source_device = info.st_rdev
    finally:
        os.close(fd)

    with _lock:
        _expire_unclaimed_jobs()
        cutoff = time.time() - JOB_TTL_SECONDS
        for stale_id in [key for key, item in _jobs.items() if item.created_at < cutoff and item.state != "streaming"]:
            _jobs.pop(stale_id, None)
        if any(job.state in {"bereit", "streaming"} for job in _jobs.values()):
            raise ValueError("Es läuft bereits eine Image-Erstellung.")
        job_id = secrets.token_urlsafe(18)
        safe_hostname = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip(".-") or "klipper"
        job = ImageJob(
            job_id=job_id,
            session_id=session_id,
            download_token=secrets.token_urlsafe(32),
            source_path=path,
            source_device=source_device,
            size_bytes=capture_size,
            device_bytes=actual_size,
            filename=f"{safe_hostname}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-sdkarte.img",
        )
        _jobs[job_id] = job
        while len(_jobs) > MAX_RETAINED_JOBS:
            oldest_id = next((key for key, item in _jobs.items() if item.state != "streaming"), None)
            if oldest_id is None:
                break
            _jobs.pop(oldest_id, None)
        return {
            "job_id": job.job_id,
            "download_url": f"/api/backup/download/{job.job_id}?token={job.download_token}",
            "filename": job.filename,
            "size_bytes": job.size_bytes,
        }


def job_status(job_id: str, session_id: str) -> dict:
    with _lock:
        _expire_unclaimed_jobs()
        job = _jobs.get(job_id)
        if not job or not hmac.compare_digest(job.session_id, session_id):
            raise KeyError(job_id)
        return {
            "job_id": job.job_id,
            "state": job.state,
            "sent_bytes": job.sent_bytes,
            "size_bytes": job.size_bytes,
            "sha256": job.sha256,
            "error": job.error,
        }


def claim_download(job_id: str, session_id: str, token: str) -> ImageJob:
    with _lock:
        _expire_unclaimed_jobs()
        job = _jobs.get(job_id)
        if (
            not job
            or not hmac.compare_digest(job.session_id, session_id)
            or not hmac.compare_digest(job.download_token, token)
            or job.download_claimed
            or job.state != "bereit"
        ):
            raise ValueError(job_id)
        job.download_claimed = True
        return job


def stream_job(job: ImageJob) -> Iterator[bytes]:
    fd = None
    digest = hashlib.sha256()
    sent = 0
    next_printer_check = time.monotonic()
    with _lock:
        if job.state != "bereit":
            raise OSError("Der Image-Auftrag ist nicht mehr bereit.")
    try:
        # Write pending data of the running system to the card before reading it.
        os.sync()
        fd = os.open(job.source_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev != job.source_device or _device_size(fd) != job.device_bytes:
            raise OSError("Identität oder Größe der microSD hat sich vor dem Lesen geändert.")
        # The whole-card device has its own page cache, which writes to the mounted
        # partition never update; drop it so no block from an earlier read is sent stale.
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        with _lock:
            job.state = "streaming"
        while sent < job.size_bytes:
            if time.monotonic() >= next_printer_check:
                if services.klipper_status()["classification"] == "active":
                    raise OSError("Moonraker meldet einen aktiven Druck; Image-Übertragung abgebrochen.")
                next_printer_check = time.monotonic() + PRINTER_RECHECK_SECONDS
            target = min(CHUNK_BYTES, job.size_bytes - sent)
            chunk = bytearray()
            while len(chunk) < target:
                data = os.read(fd, target - len(chunk))
                if not data:
                    raise OSError(f"SD-Karte lieferte nur {sent + len(chunk)} von {job.size_bytes} Bytes.")
                chunk.extend(data)
            payload = bytes(chunk)
            digest.update(payload)
            sent += len(payload)
            with _lock:
                job.sent_bytes = sent
            yield payload
        with _lock:
            job.sha256 = digest.hexdigest()
            job.state = "übertragen"
    except GeneratorExit:
        with _lock:
            job.state = "abgebrochen"
            job.error = "Browser hat die Übertragung beendet. Die Image-Datei ist unvollständig."
        raise
    except Exception:
        with _lock:
            job.state = "fehler"
            job.error = "Lesefehler oder Druckerstatus geändert. Die Image-Datei ist unvollständig."
        raise
    finally:
        if fd is not None:
            os.close(fd)
