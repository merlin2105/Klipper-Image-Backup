from __future__ import annotations

import json
import logging
import os
import platform
import stat
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeError):
        return None


def system_info() -> dict[str, Any]:
    model = _read_text("/proc/device-tree/model")
    if model:
        model = model.replace("\x00", "").strip()
    os_release: dict[str, str] = {}
    for line in (_read_text("/etc/os-release") or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    return {
        "hostname": socket.gethostname(),
        "model": model or "Nicht erkannt",
        "os": os_release.get("PRETTY_NAME", platform.platform()),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "service_mode": "normal",
        "recovery_available": False,
    }


def _flatten_devices(devices: list[dict[str, Any]], parent: str | None = None) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for device in devices:
        name = str(device.get("name", ""))
        path = str(device.get("path") or f"/dev/{name}")
        item = {
            "name": name,
            "path": path,
            "parent": parent,
            "type": device.get("type"),
            "size_bytes": device.get("size"),
            "filesystem": device.get("fstype"),
            "mountpoints": device.get("mountpoints") or ([device["mountpoint"]] if device.get("mountpoint") else []),
            "model": device.get("model"),
            "removable": bool(device.get("rm", False)),
            "read_only": bool(device.get("ro", False)),
        }
        flattened.append(item)
        flattened.extend(_flatten_devices(device.get("children") or [], path))
    return flattened


def storage_info() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--bytes", "--output", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,RM,RO"],
            check=True,
            capture_output=True,
            text=True,
            timeout=4,
        )
        devices = json.loads(result.stdout).get("blockdevices", [])
        flat = _flatten_devices(devices)
        root = next((item for item in flat if "/" in item["mountpoints"]), None)
        root_disk = root
        while root_disk and root_disk["type"] != "disk":
            root_disk = next((item for item in flat if item["path"] == root_disk["parent"]), None)
        return {
            "available": True,
            "devices": flat,
            "system_device": root_disk,
            "filesystem_usage": _root_usage(),
            "estimated_minimum_image_bytes": None,
            "shrinking_available": False,
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        logger.exception("Could not read block devices with lsblk")
        return {
            "available": False,
            "devices": [],
            "system_device": None,
            "filesystem_usage": _root_usage(),
            "estimated_minimum_image_bytes": None,
            "shrinking_available": False,
            "reason": "Blockgeräte konnten nicht sicher gelesen werden.",
        }


def _root_usage() -> dict[str, int] | None:
    try:
        usage = shutil.disk_usage("/")
        return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}
    except OSError:
        return None


def klipper_status() -> dict[str, Any]:
    try:
        headers = {"Accept": "application/json"}
        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:7125/printer/info", headers=headers), timeout=2) as response:
            info = json.loads(response.read(1024 * 1024)).get("result", {})
        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:7125/printer/objects/query?print_stats", headers=headers), timeout=2) as response:
            payload = json.loads(response.read(1024 * 1024))
        stats = payload.get("result", {}).get("status", {}).get("print_stats")
        if not isinstance(stats, dict) or not isinstance(stats.get("state"), str):
            raise ValueError("Moonraker returned no print state")
        state = stats["state"].lower()
        klippy_state = info.get("klippy_state") or info.get("state")
        safe_states = {"standby", "complete", "cancelled", "idle"}
        active_states = {"printing", "paused"}
        if state in active_states:
            classification = "active"
            safe = False
        elif state in safe_states and klippy_state == "ready":
            classification = "ready"
            safe = True
        else:
            classification = "unknown"
            safe = False
        return {
            "available": True,
            "state": state,
            "klippy_state": klippy_state,
            "classification": classification,
            "safe_to_backup": safe,
            "message": "Druckauftrag aktiv oder pausiert." if classification == "active" else (
                "Drucker bereit." if safe else "Druckerstatus ist unbekannt; Backup gesperrt."
            ),
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return {
            "available": False,
            "state": None,
            "klippy_state": None,
            "classification": "unknown",
            "safe_to_backup": False,
            "message": "Moonraker nicht erreichbar oder Status ungültig; Backup gesperrt.",
        }


def backup_preflight() -> dict[str, Any]:
    printer = klipper_status()
    storage = storage_info()
    blockers = []
    if not printer["safe_to_backup"]:
        blockers.append("Der Drucker muss verbunden und im Standby sein.")
    device = storage.get("system_device")
    supported_layout = False
    source_readable = False
    if not storage["available"] or not device:
        blockers.append("Systemlaufwerk konnte nicht eindeutig erkannt werden.")
    elif device.get("path") != "/dev/mmcblk0" or device.get("type") != "disk":
        blockers.append("Nur die interne microSD unter /dev/mmcblk0 wird derzeit unterstützt.")
    else:
        partitions = [item for item in storage["devices"] if item.get("parent") == device["path"]]
        boot = next((item for item in partitions if "/boot" in item["mountpoints"]), None)
        root = next((item for item in partitions if "/" in item["mountpoints"]), None)
        boot_ok = bool(boot and boot.get("filesystem") in {"vfat", "fat", "fat32"})
        root_ok = bool(root and root.get("filesystem") == "ext4")
        supported_layout = boot_ok and root_ok
        if not boot_ok:
            blockers.append("Die FAT-Bootpartition konnte nicht sicher erkannt werden.")
        if not root_ok:
            blockers.append("Die ext4-Systempartition konnte nicht sicher erkannt werden.")
        try:
            fd = os.open(device["path"], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                if not stat.S_ISBLK(os.fstat(fd).st_mode):
                    blockers.append("Das erkannte Systemlaufwerk ist kein Blockgerät.")
                else:
                    source_readable = True
            finally:
                os.close(fd)
        except OSError:
            blockers.append("KIBM hat keinen lesenden Zugriff auf die System-microSD.")
    return {
        "backup_available": not blockers,
        "safe_to_prepare_recovery": False,
        "printer": printer,
        "storage": storage,
        "blockers": blockers,
        "capture_mode": "full_raw_live_image",
        "image_size_bytes": device.get("size_bytes") if device else None,
        "checks": {
            "printer_ready": printer["safe_to_backup"],
            "system_card_supported": bool(device and device.get("path") == "/dev/mmcblk0" and supported_layout),
            "read_access": source_readable,
        },
    }
