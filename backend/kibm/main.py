from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import services

APP_DIR = Path(__file__).parent
app = FastAPI(title="Klipper Image Backup Manager", version="0.1.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


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


@app.get("/api/recovery/status")
async def get_recovery_status():
    return {
        "installed": False,
        "mode": "normal",
        "boot_prototype_tested": False,
        "message": "Recovery-System ist noch nicht verfügbar.",
    }
