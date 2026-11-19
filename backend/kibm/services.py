from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


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
            "mountpoints": device.get("mountpoints") or [],
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
            ["lsblk", "--json", "--bytes", "--output", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,RM,RO"],
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
        klippy_state = info.get("klippy_state")
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
            "safe_to_restart": safe,
            "message": "Druckauftrag aktiv oder pausiert." if classification == "active" else (
                "Drucker bereit." if safe else "Druckerstatus ist unbekannt; Neustart gesperrt."
            ),
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return {
            "available": False,
            "state": None,
            "klippy_state": None,
            "classification": "unknown",
            "safe_to_restart": False,
            "message": "Moonraker nicht erreichbar oder Status ungültig; Neustart gesperrt.",
        }


def backup_preflight() -> dict[str, Any]:
    printer = klipper_status()
    storage = storage_info()
    blockers = ["Backup-Funktion ist in dieser Version nicht verfügbar."]
    if not printer["safe_to_restart"]:
        blockers.append("Druckerstatus erlaubt keinen sicheren Neustart.")
    if not storage["available"] or not storage["system_device"]:
        blockers.append("Systemlaufwerk konnte nicht eindeutig erkannt werden.")
    return {
        "backup_available": False,
        "safe_to_prepare_recovery": False,
        "printer": printer,
        "storage": storage,
        "blockers": blockers,
    }
