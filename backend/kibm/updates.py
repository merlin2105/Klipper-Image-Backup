from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPOSITORY = "merlin2105/Klipper-Image-Backup"
BUILD_INFO = Path("/opt/klipper-image-backup-manager/build-info.json")
CACHE_SECONDS = 300
_cached_at = 0.0
_cached_commit: dict[str, str] | None = None
_cached_error: str | None = None


def _build_info() -> dict[str, Any]:
    try:
        value = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_commit() -> tuple[dict[str, str] | None, str | None]:
    global _cached_at, _cached_commit, _cached_error
    if time.monotonic() - _cached_at < CACHE_SECONDS:
        return _cached_commit, _cached_error
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/commits/main",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "KIBM-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read(1024 * 1024))
        if not isinstance(result, dict):
            raise ValueError("GitHub hat eine ungültige Antwort geliefert.")
        sha = result.get("sha", "")
        if not isinstance(sha, str) or len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("GitHub hat keine gültige Commit-ID geliefert.")
        commit = result.get("commit", {})
        if not isinstance(commit, dict):
            commit = {}
        message = commit.get("message", "") if isinstance(commit, dict) else ""
        committer = commit.get("committer", {})
        if not isinstance(committer, dict):
            committer = {}
        _cached_commit = {
            "sha": sha,
            "date": str(committer.get("date", "")),
            "message": str(message).splitlines()[0][:160],
        }
        _cached_error = None
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as error:
        _cached_commit = None
        _cached_error = "GitHub ist derzeit nicht erreichbar." if isinstance(error, OSError) else str(error)
    _cached_at = time.monotonic()
    return _cached_commit, _cached_error


def status(worker_status: dict[str, Any] | None) -> dict[str, Any]:
    build = _build_info()
    current = build.get("commit")
    dirty = build.get("dirty") is True
    latest, error = _latest_commit()
    available = bool(latest and (current != latest["sha"] or dirty))
    worker = worker_status or {"available": False, "state": "unavailable"}
    return {
        "current_commit": current if isinstance(current, str) else None,
        "current_dirty": dirty,
        "latest_commit": latest,
        "update_available": available,
        "can_update": bool(available and worker.get("available") is True and worker.get("state") != "running"),
        "check_error": error,
        "worker": worker,
    }
