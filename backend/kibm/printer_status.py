"""Backup progress on the printer: Klipper status line and KlipperScreen banner.

The media worker hands every job change to `Notifier.update`, which never blocks;
a background thread talks to Moonraker, so a slow or missing Moonraker never
slows a backup down. Every Moonraker error is ignored.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

MOONRAKER = "http://127.0.0.1:7125"
DISPLAY_INTERVAL_SECONDS = 10
CLEAR_AFTER_SECONDS = 60
_GIB = 1024 ** 3
_UNSAFE = re.compile(r'["#;\x00-\x1f\x7f]')
_FINAL = {"fertig", "fehler", "abgebrochen"}
_FIXED = {
    "vorbereitung": "Sicherung startet",
    "formatieren": "Sicherung startet",
    "dateisystem": "Dateisystem wird geprüft",
    "verkleinern": "Image wird verkleinert",
    "fertig": "Sicherung fertig",
    "fehler": "Sicherung fehlgeschlagen",
    "abgebrochen": "Sicherung abgebrochen",
}
logger = logging.getLogger(__name__)


def gcode_text(text: str) -> str:
    """Drop characters that would end the G-code parameter or start a comment."""
    return _UNSAFE.sub("", text)


def _gb(value: int) -> str:
    return f"{value / _GIB:.1f}".replace(".", ",")


def _percent(done: int, total: int) -> int:
    return min(100, int(done * 100 / total)) if total > 0 else 0


def _describe(job: dict[str, Any]) -> tuple[Optional[str], Optional[int]]:
    phase = job.get("phase")
    if phase in _FIXED:
        return f"KIBM: {_FIXED[phase]}", None
    if phase == "pruefsumme":
        if job.get("state") == "schrumpfen":
            percent = _percent(job.get("shrink_bytes", 0), job.get("shrink_total_bytes", 0))
        else:
            percent = _percent(job.get("verified_bytes", 0), job.get("image_size_bytes", 0))
        return f"KIBM: {percent} % Prüfsumme", percent
    if phase == "sichern":
        done, total, label = job.get("sent_bytes", 0), job.get("image_size_bytes", 0), "gesichert"
    elif phase == "verifizieren":
        done, total, label = job.get("verified_bytes", 0), job.get("image_size_bytes", 0), "geprüft"
    elif phase == "kopie":
        done, total, label = job.get("shrink_bytes", 0), job.get("shrink_total_bytes", 0), "Arbeitskopie"
    else:
        return None, None
    percent = _percent(done, total)
    return f"KIBM: {percent} % {label} ({_gb(done)} von {_gb(total)} GB)", percent


def status_text(job: dict[str, Any]) -> Optional[str]:
    return _describe(job)[0]


class Notifier:
    def __init__(
        self,
        send: Callable[[str], None],
        has_respond: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
        start_thread: bool = True,
    ) -> None:
        self._send = send
        self._has_respond = has_respond
        self._clock = clock
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._job_id: Optional[str] = None
        self._phase: Optional[str] = None
        self._final = False
        self._last_display = 0.0
        self._last_text: Optional[str] = None
        self._respond_step: Optional[int] = None
        self._clear_at: Optional[float] = None
        self._respond_job: Optional[str] = None
        self._respond_ok = False
        if start_thread:
            threading.Thread(target=self._loop, name="kibm-printer-status", daemon=True).start()

    def update(self, job: dict[str, Any]) -> None:
        text, percent = _describe(job)
        if text is None:
            return
        text = gcode_text(text)
        phase = job.get("phase")
        with self._lock:
            if job.get("job_id") != self._job_id:
                self._job_id, self._phase, self._final = job.get("job_id"), None, False
                self._respond_step, self._last_text, self._clear_at = None, None, None
            if self._final:
                return
            now = self._clock()
            phase_changed = phase != self._phase
            self._phase = phase
            if phase_changed or (now - self._last_display >= DISPLAY_INTERVAL_SECONDS and text != self._last_text):
                self._last_display, self._last_text = now, text
                self._put(("display", text, self._job_id))
            step = None if percent is None else percent // 10
            if phase_changed or step != self._respond_step:
                self._respond_step = step
                self._put(("respond", text, self._job_id))
            if phase in _FINAL:
                self._final = True
                self._clear_at = now + CLEAR_AFTER_SECONDS

    def drain(self) -> None:
        """Send everything queued and a due clear now (tests; the thread does this itself)."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle(item)
        self._clear_if_due()

    def _put(self, item: tuple[str, str, Optional[str]]) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            pass

    def _loop(self) -> None:
        while True:
            try:
                self._handle(self._queue.get(timeout=1))
            except queue.Empty:
                pass
            self._clear_if_due()

    def _handle(self, item: tuple[str, str, Optional[str]]) -> None:
        kind, text, job_id = item
        if kind == "display":
            self._safe_send(f'SET_DISPLAY_TEXT MSG="{text}"')
            return
        if job_id != self._respond_job:
            self._respond_job = job_id
            try:
                self._respond_ok = bool(self._has_respond())
            except Exception:
                self._respond_ok = False
        if self._respond_ok:
            self._safe_send(f'RESPOND MSG="{text}"')

    def _clear_if_due(self) -> None:
        with self._lock:
            due = self._clear_at is not None and self._clock() >= self._clear_at
            if due:
                self._clear_at = None
        if due:
            self._safe_send("SET_DISPLAY_TEXT")

    def _safe_send(self, script: str) -> None:
        try:
            self._send(script)
        except Exception as exc:  # the printer display is a convenience, never a reason to fail
            logger.debug("Printer status not sent: %s", exc)


def moonraker_send(script: str) -> None:
    request = urllib.request.Request(
        f"{MOONRAKER}/printer/gcode/script", data=json.dumps({"script": script}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read(64 * 1024)


def moonraker_has_respond() -> bool:
    with urllib.request.urlopen(f"{MOONRAKER}/printer/gcode/help", timeout=2) as response:
        commands = json.loads(response.read(1024 * 1024)).get("result", {})
    return isinstance(commands, dict) and "RESPOND" in commands
