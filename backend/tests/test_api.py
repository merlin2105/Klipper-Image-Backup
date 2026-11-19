from fastapi.testclient import TestClient

from kibm import services
from kibm.main import app


client = TestClient(app)


def test_dashboard_is_available_without_auth() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Systemstatus" in response.text


def test_favicon_uses_the_static_svg() -> None:
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/static/favicon.svg"


def test_read_only_api_is_available_without_login() -> None:
    assert client.get("/api/system").status_code == 200
    assert client.get("/api/storage").status_code == 200
    assert client.get("/api/klipper/status").status_code == 200


def test_preflight_is_read_only_and_never_claims_backup_ready(monkeypatch) -> None:
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_restart": True, "classification": "ready"})
    monkeypatch.setattr(services, "storage_info", lambda: {"available": True, "system_device": {"path": "/dev/mmcblk0"}})
    result = client.get("/api/backup/preflight").json()
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
