from __future__ import annotations

import hmac
import json
import secrets
import socket
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import image_stream, services

APP_DIR = Path(__file__).parent
AUTH_SOCKET = "/run/kibm-auth/auth.sock"
SESSION_TTL_SECONDS = 4 * 60 * 60
sessions: dict[str, tuple[str, float]] = {}
login_attempts: dict[str, list[float]] = {}
app = FastAPI(title="Klipper Image Backup Manager", version="0.1.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class BackupRequest(BaseModel):
    confirmed: bool


def _session(request: Request) -> tuple[str, str]:
    cookie = request.cookies.get("kibm_session", "")
    entry = sessions.get(cookie)
    if not entry or entry[1] < time.time():
        sessions.pop(cookie, None)
        raise HTTPException(status_code=401, detail="Bitte mit dem bestehenden Pi-Konto anmelden.")
    return cookie, entry[0]


def _require_csrf(request: Request, csrf_header: Optional[str]) -> tuple[str, str]:
    session_id, expected = _session(request)
    origin = request.headers.get("origin")
    if not origin or origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Ungültiger Origin.")
    if not csrf_header or not hmac.compare_digest(expected, csrf_header):
        raise HTTPException(status_code=403, detail="Ungültiges CSRF-Token.")
    return session_id, expected


def _authenticate_pi_password(password: str) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as auth_socket:
        auth_socket.settimeout(5)
        auth_socket.connect(AUTH_SOCKET)
        auth_socket.sendall(json.dumps({"password": password}).encode("utf-8") + b"\n")
        with auth_socket.makefile("rb") as stream:
            response = stream.readline(256)
    result = json.loads(response)
    if result.get("error") == "authentication_unavailable":
        raise RuntimeError("Pi password authentication is unavailable")
    return result.get("authenticated") is True


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    return response


@app.get("/")
async def dashboard():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url="/static/favicon.svg")


@app.get("/api/system")
async def get_system():
    return services.system_info()


@app.get("/api/storage")
async def get_storage():
    return services.storage_info()


@app.get("/api/klipper/status")
async def get_klipper_status():
    return services.klipper_status()


@app.get("/api/backup/preflight")
async def get_backup_preflight():
    return services.backup_preflight()


@app.get("/api/session")
async def get_session(request: Request):
    try:
        _, csrf = _session(request)
        return {"authenticated": True, "csrf_token": csrf}
    except HTTPException as exc:
        if exc.status_code == 401:
            return {"authenticated": False}
        raise


@app.post("/api/login")
async def login(body: LoginRequest, request: Request, response: Response):
    origin = request.headers.get("origin")
    if not origin or origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Ungültiger Origin.")
    client_address = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [stamp for stamp in login_attempts.get(client_address, []) if stamp > now - 600]
    if len(attempts) >= 5:
        login_attempts[client_address] = attempts
        raise HTTPException(status_code=429, detail="Zu viele Anmeldeversuche. Bitte in zehn Minuten erneut versuchen.")
    try:
        authenticated = _authenticate_pi_password(body.password)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Pi-Anmeldung ist nicht erreichbar.") from exc
    if not authenticated:
        attempts.append(now)
        login_attempts[client_address] = attempts
        raise HTTPException(status_code=401, detail="Das Passwort des Pi-Kontos ist falsch.")
    login_attempts.pop(client_address, None)
    session_id = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    sessions[session_id] = (csrf, time.time() + SESSION_TTL_SECONDS)
    response.set_cookie(
        "kibm_session", session_id, httponly=True, secure=request.url.scheme == "https",
        samesite="strict", max_age=SESSION_TTL_SECONDS, path="/",
    )
    return {"authenticated": True, "csrf_token": csrf}


@app.post("/api/logout")
async def logout(request: Request, response: Response, x_kibm_csrf: Optional[str] = Header(default=None)):
    session_id, _ = _require_csrf(request, x_kibm_csrf)
    sessions.pop(session_id, None)
    response.delete_cookie("kibm_session", path="/", httponly=True, samesite="strict")
    return {"authenticated": False}


@app.post("/api/backup/start")
async def start_backup(
    body: BackupRequest,
    request: Request,
    x_kibm_csrf: Optional[str] = Header(default=None),
):
    session_id, _ = _require_csrf(request, x_kibm_csrf)
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Bestätige zuerst die Erstellung des vollständigen SD-Karten-Images.")
    preflight = services.backup_preflight()
    if not preflight["backup_available"]:
        raise HTTPException(status_code=409, detail=" ".join(preflight["blockers"]))
    try:
        job = image_stream.create_job(session_id, preflight["storage"]["system_device"])
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job


@app.get("/api/backup/jobs/{job_id}")
async def backup_job_status(job_id: str, request: Request):
    session_id, _ = _session(request)
    try:
        return image_stream.job_status(job_id, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Image-Auftrag nicht gefunden.") from exc


@app.get("/api/backup/download/{job_id}")
async def download_backup(job_id: str, request: Request, token: str):
    session_id, _ = _session(request)
    try:
        job = image_stream.claim_download(job_id, session_id, token)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Download nicht verfügbar.") from exc
    return StreamingResponse(
        image_stream.stream_job(job),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{job.filename}"',
            "Content-Length": str(job.size_bytes),
            "Cache-Control": "no-store",
            "Accept-Ranges": "none",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/recovery/status")
async def get_recovery_status():
    return {
        "installed": False,
        "mode": "normal",
        "boot_prototype_tested": False,
        "message": "Recovery-System ist noch nicht verfügbar.",
    }
