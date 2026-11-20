from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPOSITORY = "merlin2105/Klipper-Image-Backup"
BUILD_INFO = Path("/opt/klipper-image-backup-manager/build-info.json")
CACHE_SECONDS = 300
ERROR_CACHE_SECONDS = 120
# Unauthenticated callers may force a refresh; keep GitHub's 60 requests/hour
# limit out of reach for anyone on the LAN.
MIN_FORCE_REFRESH_SECONDS = 120
PROJECT_FILE = Path(__file__).resolve().parents[2] / "pyproject.toml"
_cached_at = 0.0
_cached_commit: dict[str, str] | None = None
_cached_error: str | None = None


def _build_info() -> dict[str, Any]:
    try:
        value = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _version_from_project(text: str) -> str:
    match = re.search(r'(?ms)^\[project\]\s.*?^version\s*=\s*["\']([^"\']+)["\']', text)
    version = match.group(1) if match else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:b\d+)?", version):
        raise ValueError("Die KIBM-Versionsnummer ist ungültig oder fehlt.")
    return version


def _version_key(version: str | None) -> tuple[int, int, int, float] | None:
    # A beta sorts before its final release: 0.3.0b2 < 0.3.0.
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:b(\d+))?", version or "")
    if not match:
        return None
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), int(beta) if beta else float("inf")


def _read_latest_release() -> dict[str, str]:
    # Updates come only from release tags; `kibm-admin update-run` verifies the
    # tag signature before installing. This only shows what is available.
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/tags?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "KIBM-updater"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read(1024 * 1024))
    if not isinstance(result, list):
        raise ValueError("GitHub hat eine ungültige Antwort geliefert.")
    best = None
    for item in result:
        name = item.get("name") if isinstance(item, dict) else None
        key = _version_key(name[1:]) if isinstance(name, str) and name.startswith("v") else None
        if key is not None and (best is None or key > best[0]):
            best = (key, item)
    if best is None:
        raise ValueError("Auf GitHub ist noch kein Release-Tag vorhanden.")
    item = best[1]
    commit = item.get("commit")
    sha = commit.get("sha", "") if isinstance(commit, dict) else ""
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("GitHub hat keine gültige Commit-ID geliefert.")
    return {"sha": sha, "tag": item["name"], "version": item["name"][1:], "message": f"Release {item['name']}", "date": ""}


def _local_version(build: dict[str, Any]) -> str | None:
    try:
        return _version_from_project(PROJECT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        version = build.get("version")
        return version if isinstance(version, str) else None


def _version_label(version: str | None) -> str | None:
    if not version:
        return None
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(b\d+)?", version)
    if not match:
        return f"v{version}"
    return f"v{match.group(1)} Beta" if match.group(2) else f"v{match.group(1)}"


def _latest_commit(*, force: bool = False) -> tuple[dict[str, str] | None, str | None]:
    global _cached_at, _cached_commit, _cached_error
    age = time.monotonic() - _cached_at
    if force and _cached_at and age < MIN_FORCE_REFRESH_SECONDS:
        force = False
    if not force and _cached_commit and age < CACHE_SECONDS:
        return _cached_commit, _cached_error
    if not force and _cached_error and age < ERROR_CACHE_SECONDS:
        return _cached_commit, _cached_error
    try:
        latest = _read_latest_release()
        _cached_commit = latest
        _cached_error = None
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as error:
        _cached_commit = None
        _cached_error = "GitHub ist derzeit nicht erreichbar." if isinstance(error, OSError) else str(error)
    _cached_at = time.monotonic()
    return _cached_commit, _cached_error


def _is_newer(latest: dict[str, str] | None, current: Any, dirty: bool, current_version: str | None) -> bool:
    if not latest:
        return False
    latest_key, current_key = _version_key(latest["version"]), _version_key(current_version)
    if latest_key is None:
        return False
    if current_key is None or latest_key == current_key:
        # Same version from an untagged or locally changed tree: offer the signed release.
        return current != latest["sha"] or dirty
    return latest_key > current_key


def status(worker_status: dict[str, Any] | None, *, force_refresh: bool = False) -> dict[str, Any]:
    build = _build_info()
    current = build.get("commit")
    dirty = build.get("dirty") is True
    current_version = _local_version(build)
    latest, error = _latest_commit(force=force_refresh)
    available = _is_newer(latest, current, dirty, current_version)
    worker = worker_status or {"available": False, "state": "unavailable"}
    return {
        "current_version": _version_label(current_version),
        "latest_version": _version_label(latest.get("version") if latest else None),
        "current_commit": current if isinstance(current, str) else None,
        "current_dirty": dirty,
        "latest_commit": latest,
        "update_available": available,
        "can_update": bool(available and worker.get("available") is True and worker.get("state") != "running"),
        "check_error": error,
        "worker": worker,
    }
