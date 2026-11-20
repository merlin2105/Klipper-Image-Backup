from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPOSITORY = "merlin2105/Klipper-Image-Backup"
BUILD_INFO = Path("/opt/klipper-image-backup-manager/build-info.json")
CACHE_SECONDS = 300
ERROR_CACHE_SECONDS = 15
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


def _read_latest_commit() -> dict[str, str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/commits/main",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "KIBM-updater"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read(1024 * 1024))
    if not isinstance(result, dict):
        raise ValueError("GitHub hat eine ungültige Antwort geliefert.")
    sha = result.get("sha", "")
    if not isinstance(sha, str) or len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ValueError("GitHub hat keine gültige Commit-ID geliefert.")
    commit = result.get("commit", {})
    commit = commit if isinstance(commit, dict) else {}
    committer = commit.get("committer", {})
    committer = committer if isinstance(committer, dict) else {}
    return {
        "sha": sha,
        "date": str(committer.get("date", "")),
        "message": str(commit.get("message", "")).splitlines()[0][:160],
    }


def _read_latest_version() -> str:
    request = urllib.request.Request(
        f"https://raw.githubusercontent.com/{REPOSITORY}/main/pyproject.toml",
        headers={"User-Agent": "KIBM-updater"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return _version_from_project(response.read(256 * 1024).decode("utf-8"))


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
    if not force and _cached_commit and age < CACHE_SECONDS:
        return _cached_commit, _cached_error
    if not force and _cached_error and age < ERROR_CACHE_SECONDS:
        return _cached_commit, _cached_error
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            commit_future = pool.submit(_read_latest_commit)
            version_future = pool.submit(_read_latest_version)
            latest = commit_future.result()
            latest["version"] = version_future.result()
        _cached_commit = latest
        _cached_error = None
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as error:
        _cached_commit = None
        _cached_error = "GitHub ist derzeit nicht erreichbar." if isinstance(error, OSError) else str(error)
    _cached_at = time.monotonic()
    return _cached_commit, _cached_error


def status(worker_status: dict[str, Any] | None, *, force_refresh: bool = False) -> dict[str, Any]:
    build = _build_info()
    current = build.get("commit")
    dirty = build.get("dirty") is True
    current_version = _local_version(build)
    latest, error = _latest_commit(force=force_refresh)
    available = bool(latest and (current != latest["sha"] or dirty))
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
