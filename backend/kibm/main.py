from __future__ import annotations

import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import services

APP_DIR = Path(__file__).parent
SESSION_TTL_SECONDS = 8 * 60 * 60
sessions: dict[str, tuple[str, float]] = {}
login_attempts: dict[str, list[float]] = {}

app = FastAPI(title="Klipper Image Backup Manager", version="0.1.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def _configured_secrets() -> tuple[str, str]:
    password = os.environ.get("KIBM_ADMIN_PASSWORD", "")
    secret = os.environ.get("KIBM_SESSION_SECRET", "")
    if len(password) < 12 or len(secret) < 32:
        raise HTTPException(status_code=503, detail="KIBM-Zugangsdaten sind nicht sicher konfiguriert.")
    return password, secret


def _session(request: Request) -> tuple[str, str]:
    cookie = request.cookies.get("kibm_session", "")
    secret = os.environ.get("KIBM_SESSION_SECRET", "")
    token, separator, signature = cookie.partition(".")
    expected_signature = hmac.new(secret.encode(), token.encode(), "sha256").hexdigest() if secret and separator else ""
    if not expected_signature or not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich.")
    entry = sessions.get(cookie)
    if not entry or entry[1] < time.time():
        sessions.pop(cookie, None)
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich.")
    return cookie, entry[0]


def _require_csrf(request: Request, csrf_header: str | None) -> None:
    _, expected = _session(request)
    origin = request.headers.get("origin")
    if not origin or origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Ungültiger Origin.")
    if not csrf_header or not hmac.compare_digest(expected, csrf_header):
        raise HTTPException(status_code=403, detail="Ungültiges CSRF-Token.")


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
    configured_password, configured_secret = _configured_secrets()
    origin = request.headers.get("origin")
    if not origin or origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Ungültiger Origin.")
    client_address = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [stamp for stamp in login_attempts.get(client_address, []) if stamp > now - 600]
    if len(attempts) >= 5:
        login_attempts[client_address] = attempts
        raise HTTPException(status_code=429, detail="Zu viele Anmeldeversuche. Bitte in zehn Minuten erneut versuchen.")
    if not hmac.compare_digest(body.password, configured_password):
        attempts.append(now)
        login_attempts[client_address] = attempts
        raise HTTPException(status_code=401, detail="Passwort ist falsch.")
    login_attempts.pop(client_address, None)
    token = secrets.token_urlsafe(32)
    signature = hmac.new(configured_secret.encode(), token.encode(), "sha256").hexdigest()
    session_id = f"{token}.{signature}"
    csrf = secrets.token_urlsafe(32)
    sessions[session_id] = (csrf, time.time() + SESSION_TTL_SECONDS)
    response.set_cookie(
        "kibm_session", session_id, httponly=True, secure=os.environ.get("KIBM_COOKIE_SECURE") == "1",
        samesite="strict", max_age=SESSION_TTL_SECONDS, path="/",
    )
    return {"authenticated": True, "csrf_token": csrf}


@app.post("/api/logout")
async def logout(request: Request, response: Response, x_kibm_csrf: str | None = Header(default=None)):
    _require_csrf(request, x_kibm_csrf)
    token = request.cookies.get("kibm_session", "")
    sessions.pop(token, None)
    response.delete_cookie("kibm_session", path="/", httponly=True, samesite="strict")
    return {"authenticated": False}


@app.get("/api/system")
async def get_system(request: Request):
    _session(request)
    return services.system_info()


@app.get("/api/storage")
async def get_storage(request: Request):
    _session(request)
    return services.storage_info()


@app.get("/api/klipper/status")
async def get_klipper_status(request: Request):
    _session(request)
    return services.klipper_status()


@app.post("/api/backup/preflight")
async def get_backup_preflight(request: Request, x_kibm_csrf: str | None = Header(default=None)):
    _require_csrf(request, x_kibm_csrf)
    return services.backup_preflight()


@app.get("/api/recovery/status")
async def get_recovery_status(request: Request):
    _session(request)
    return {
        "installed": False,
        "mode": "normal",
        "boot_prototype_tested": False,
        "message": "Recovery-System ist noch nicht verfügbar.",
    }
