from fastapi.testclient import TestClient
from types import SimpleNamespace
import stat

from kibm import main, services
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


def test_preflight_requires_supported_source_and_does_not_claim_recovery(monkeypatch) -> None:
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_backup": True, "classification": "ready"})
    monkeypatch.setattr(services, "storage_info", lambda: {"available": True, "system_device": {"path": "/dev/mmcblk0"}})
    result = client.get("/api/backup/preflight").json()
    assert result["backup_available"] is False
    assert result["safe_to_prepare_recovery"] is False


def test_preflight_allows_supported_idle_micro_sd(monkeypatch) -> None:
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_backup": True, "classification": "ready"})
    disk = {"path": "/dev/mmcblk0", "type": "disk", "size_bytes": 32_000_000_000}
    partitions = [
        {"path": "/dev/mmcblk0p1", "parent": "/dev/mmcblk0", "mountpoints": ["/boot"], "filesystem": "vfat"},
        {"path": "/dev/mmcblk0p2", "parent": "/dev/mmcblk0", "mountpoints": ["/"], "filesystem": "ext4"},
    ]
    monkeypatch.setattr(services, "storage_info", lambda: {"available": True, "system_device": disk, "devices": partitions})
    monkeypatch.setattr(services.os, "open", lambda *args, **kwargs: 9)
    monkeypatch.setattr(services.os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFBLK))
    monkeypatch.setattr(services.os, "close", lambda _fd: None)
    result = services.backup_preflight()
    assert result["backup_available"] is True
    assert result["image_size_bytes"] == 32_000_000_000
    assert result["checks"] == {"printer_ready": True, "system_card_supported": True, "read_access": True}


def test_printer_unknown_state_blocks_backup(monkeypatch) -> None:
    def fail(*args, **kwargs): raise OSError("offline")
    monkeypatch.setattr(services.urllib.request, "urlopen", fail)
    result = services.klipper_status()
    assert result["classification"] == "unknown"
    assert result["safe_to_backup"] is False


def test_active_print_blocks_backup(monkeypatch) -> None:
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
    assert result["safe_to_backup"] is False


def test_non_ready_klippy_blocks_backup_even_if_no_print_is_running(monkeypatch) -> None:
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
    assert result["safe_to_backup"] is False


def test_backup_job_endpoints_require_authentication() -> None:
    assert client.get("/api/backup/jobs/not-a-job").status_code == 401
    assert client.post("/api/backup/start", json={"confirmed": True}).status_code == 401


def test_backup_authentication_uses_existing_pi_password_and_csrf(monkeypatch) -> None:
    monkeypatch.setattr(main, "_authenticate_pi_password", lambda password: password == "existing-password")
    response = client.post(
        "/api/login", json={"password": "existing-password"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 200
    assert response.headers["set-cookie"].startswith("kibm_session=")
    assert "httponly" in response.headers["set-cookie"].lower()
    csrf = response.json()["csrf_token"]
    assert client.post(
        "/api/backup/start", json={"confirmed": True}, headers={"Origin": "http://testserver"}
    ).status_code == 403
    started = client.post(
        "/api/backup/start",
        json={"confirmed": True},
        headers={"Origin": "http://testserver", "X-KIBM-CSRF": csrf},
    )
    assert started.status_code == 409
    assert "Standby" in started.json()["detail"]
    assert client.post(
        "/api/logout", headers={"Origin": "http://testserver", "X-KIBM-CSRF": csrf}
    ).status_code == 200


def test_authenticated_session_cookie_is_secure_over_https(monkeypatch) -> None:
    monkeypatch.setattr(main, "_authenticate_pi_password", lambda _password: True)
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="https://testserver") as https_client:
        response = https_client.post(
            "/api/login", json={"password": "existing-password"}, headers={"Origin": "https://testserver"}
        )
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()
