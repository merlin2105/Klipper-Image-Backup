import os

os.environ.setdefault("KIBM_ADMIN_PASSWORD", "test-password-long-enough")
os.environ.setdefault("KIBM_SESSION_SECRET", "t" * 48)

from fastapi.testclient import TestClient

from kibm import services
from kibm.main import app


client = TestClient(app)


def login() -> tuple[str, dict[str, str]]:
    response = client.post("/api/login", json={"password": os.environ["KIBM_ADMIN_PASSWORD"]}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    return response.json()["csrf_token"], {"Origin": "http://testserver"}


def test_dashboard_is_available_without_auth() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Systemstatus" in response.text


def test_favicon_uses_the_static_svg() -> None:
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/static/favicon.svg"


def test_private_api_requires_session() -> None:
    assert client.get("/api/system").status_code == 401


def test_login_issues_csrf_token_and_protected_reads_work() -> None:
    csrf, _ = login()
    response = client.get("/api/session")
    assert response.json() == {"authenticated": True, "csrf_token": csrf}
    assert client.get("/api/system").status_code == 200


def test_preflight_requires_csrf_and_never_claims_backup_ready(monkeypatch) -> None:
    csrf, origin = login()
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_restart": True, "classification": "ready"})
    monkeypatch.setattr(services, "storage_info", lambda: {"available": True, "system_device": {"path": "/dev/mmcblk0"}})
    assert client.post("/api/backup/preflight", json={}, headers=origin).status_code == 403
    result = client.post("/api/backup/preflight", json={}, headers={**origin, "X-KIBM-CSRF": csrf}).json()
    assert result["backup_available"] is False
    assert result["safe_to_prepare_recovery"] is False


def test_printer_unknown_state_blocks_restart(monkeypatch) -> None:
    def fail(*args, **kwargs): raise OSError("offline")
    monkeypatch.setattr(services.urllib.request, "urlopen", fail)
    result = services.klipper_status()
    assert result["classification"] == "unknown"
    assert result["safe_to_restart"] is False


def test_active_print_blocks_restart(monkeypatch) -> None:
    class Response:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self, _): return self.body

    responses = iter([
        b'{"result":{"klippy_state":"ready"}}',
        b'{"result":{"status":{"print_stats":{"state":"printing"}}}}',
    ])
    monkeypatch.setattr(services.urllib.request, "urlopen", lambda *args, **kwargs: Response(next(responses)))
    result = services.klipper_status()
    assert result["classification"] == "active"
    assert result["safe_to_restart"] is False


def test_non_ready_klippy_blocks_restart_even_if_no_print_is_running(monkeypatch) -> None:
    class Response:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self, _): return self.body

    responses = iter([
        b'{"result":{"klippy_state":"shutdown"}}',
        b'{"result":{"status":{"print_stats":{"state":"standby"}}}}',
    ])
    monkeypatch.setattr(services.urllib.request, "urlopen", lambda *args, **kwargs: Response(next(responses)))
    result = services.klipper_status()
    assert result["classification"] == "unknown"
    assert result["safe_to_restart"] is False
