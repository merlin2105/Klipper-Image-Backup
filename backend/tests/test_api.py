from fastapi.testclient import TestClient
from types import SimpleNamespace
import stat

from kibm import main, services
from kibm.main import app


client = TestClient(app)


def test_dashboard_is_available_without_auth() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Klipper Image Backup" in response.text


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


def _mbr_ending_at(end_sector: int, signature: bytes = b"\x55\xaa") -> bytes:
    import struct
    mbr = bytearray(512)
    mbr[446 + 4] = 0x0C
    struct.pack_into("<II", mbr, 446 + 8, 8192, 524288)
    mbr[462 + 4] = 0x83
    struct.pack_into("<II", mbr, 462 + 8, 532480, end_sector - 532480)
    mbr[510:512] = signature
    return bytes(mbr)


def test_preflight_blocks_an_unreadable_partition_table(monkeypatch) -> None:
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_backup": True, "classification": "ready"})
    disk = {"path": "/dev/mmcblk0", "type": "disk", "size_bytes": 32_000_000_000}
    parts = [
        {"path": "/dev/mmcblk0p1", "parent": "/dev/mmcblk0", "mountpoints": ["/boot"], "filesystem": "vfat"},
        {"path": "/dev/mmcblk0p2", "parent": "/dev/mmcblk0", "mountpoints": ["/"], "filesystem": "ext4"},
    ]
    monkeypatch.setattr(services, "storage_info", lambda: {"available": True, "system_device": disk, "devices": parts})
    monkeypatch.setattr(services.os, "open", lambda *args, **kwargs: 9)
    monkeypatch.setattr(services.os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFBLK))
    monkeypatch.setattr(services.os, "close", lambda _fd: None)
    monkeypatch.setattr(services.os, "pread", lambda _fd, _count, _offset: _mbr_ending_at(62333952, b"\0\0"))
    result = services.backup_preflight()
    assert result["backup_available"] is False
    assert any("Partitionstabelle" in blocker for blocker in result["blockers"])


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
    monkeypatch.setattr(services.os, "pread", lambda _fd, _count, _offset: _mbr_ending_at(62333952))
    result = services.backup_preflight()
    assert result["backup_available"] is True
    # Only up to the end of the last partition, not the whole 32 GB card.
    assert result["image_size_bytes"] == 31_914_983_424
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
    monkeypatch.setattr(main, "_ensure_update_idle", lambda: None)
    monkeypatch.setattr(
        services,
        "backup_preflight",
        lambda: {"backup_available": False, "blockers": ["Druckauftrag läuft oder ist pausiert"]},
    )
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
    assert "Druckauftrag läuft oder ist pausiert" in started.json()["detail"]
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


def test_slow_pi_login_does_not_block_other_requests(monkeypatch) -> None:
    import asyncio
    import time

    import httpx

    def slow_password_check(_password):
        time.sleep(0.5)
        return False

    monkeypatch.setattr(main, "_authenticate_pi_password", slow_password_check)
    monkeypatch.setattr(main, "login_attempts", {})

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            login = asyncio.create_task(async_client.post(
                "/api/login", json={"password": "wrong"}, headers={"Origin": "http://testserver"}
            ))
            await asyncio.sleep(0.05)
            started = time.monotonic()
            session = await async_client.get("/api/session")
            assert session.status_code == 200
            assert time.monotonic() - started < 0.3, "The event loop was blocked by the PAM check."
            assert not login.done()
            assert (await login).status_code == 401

    asyncio.run(scenario())


def test_routes_with_blocking_io_run_in_the_threadpool() -> None:
    import inspect

    blocking_routes = {
        ("POST", "/api/login"), ("POST", "/api/backup/start"), ("GET", "/api/backup/download/{job_id}"),
        ("GET", "/api/storage"), ("GET", "/api/klipper/status"), ("GET", "/api/backup/preflight"),
        ("GET", "/api/backup/usb/devices"), ("POST", "/api/backup/usb/start"),
        ("GET", "/api/backup/usb/jobs/{job_id}"), ("GET", "/api/backup/usb/download/{job_id}/{filename}"),
        ("POST", "/api/backup/usb/eject"), ("GET", "/api/system"),
    }
    found = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()):
            if (method, route.path) in blocking_routes:
                found.add((method, route.path))
                assert not inspect.iscoroutinefunction(route.endpoint), f"{method} {route.path} blocks the event loop"
    assert found == blocking_routes


def test_parallel_login_attempts_respect_the_rate_limit(monkeypatch) -> None:
    import asyncio
    import threading
    import time

    import httpx

    calls = []
    calls_lock = threading.Lock()

    def slow_wrong_password(_password):
        with calls_lock:
            calls.append(1)
        time.sleep(0.2)
        return False

    monkeypatch.setattr(main, "_authenticate_pi_password", slow_wrong_password)
    monkeypatch.setattr(main, "login_attempts", {})

    async def scenario() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            responses = await asyncio.gather(*[
                async_client.post("/api/login", json={"password": "wrong"}, headers={"Origin": "http://testserver"})
                for _ in range(10)
            ])
        return [response.status_code for response in responses]

    statuses = asyncio.run(scenario())
    assert len(calls) == 5
    assert statuses.count(401) == 5
    assert statuses.count(429) == 5


def test_update_start_passes_the_confirmed_commit_to_the_worker(monkeypatch) -> None:
    confirmed_sha = "a" * 40
    sent = []

    def fake_media_request(payload):
        sent.append(payload)
        if payload["action"] == "update_status":
            return {"available": True, "state": "idle"}
        return {"state": "running"}

    monkeypatch.setattr(main, "_authenticate_pi_password", lambda _password: True)
    monkeypatch.setattr(main, "login_attempts", {})
    monkeypatch.setattr(main, "_media_request", fake_media_request)
    monkeypatch.setattr(main.image_stream, "has_active_job", lambda: False)
    monkeypatch.setattr(main.updates, "status", lambda _worker, **_kwargs: {
        "update_available": True, "can_update": True, "latest_commit": {"sha": confirmed_sha},
    })
    with TestClient(app) as update_client:
        csrf = update_client.post(
            "/api/login", json={"password": "existing-password"}, headers={"Origin": "http://testserver"}
        ).json()["csrf_token"]
        response = update_client.post(
            "/api/update", json={"confirmed": True},
            headers={"Origin": "http://testserver", "X-KIBM-CSRF": csrf},
        )
    assert response.status_code == 200
    assert sent[-1] == {"action": "update_start", "commit": confirmed_sha}


def _logged_in_client(monkeypatch) -> tuple[TestClient, str]:
    monkeypatch.setattr(main, "_authenticate_pi_password", lambda _password: True)
    monkeypatch.setattr(main, "login_attempts", {})
    logged_in = TestClient(app)
    csrf = logged_in.post(
        "/api/login", json={"password": "existing-password"}, headers={"Origin": "http://testserver"}
    ).json()["csrf_token"]
    return logged_in, csrf


def test_login_purges_expired_sessions_and_orphaned_usb_jobs(monkeypatch) -> None:
    monkeypatch.setattr(main, "sessions", {"expired": ("csrf", 1.0)})
    monkeypatch.setattr(main, "usb_jobs", {"old-job": {"expired": "token"}})
    _logged_in_client(monkeypatch)
    assert "expired" not in main.sessions
    assert "old-job" not in main.usb_jobs


def test_usb_job_can_be_resumed_by_a_new_session(monkeypatch) -> None:
    job = {"job_id": "job-1", "state": "sichern", "message": "läuft", "files": [], "error": None}
    monkeypatch.setattr(main, "sessions", {})
    monkeypatch.setattr(main, "usb_jobs", {})
    monkeypatch.setattr(main, "_media_request", lambda payload: (
        {"job": job} if payload["action"] in {"current", "status"} else {}
    ))
    first, _ = _logged_in_client(monkeypatch)
    second, _ = _logged_in_client(monkeypatch)

    resumed = first.get("/api/backup/usb/current").json()["job"]
    again = second.get("/api/backup/usb/current").json()["job"]

    assert resumed["job_id"] == "job-1"
    assert resumed["download_token"] != again["download_token"]
    assert first.get("/api/backup/usb/current").json()["job"]["download_token"] == resumed["download_token"]
    assert first.get("/api/backup/usb/jobs/job-1").status_code == 200
    assert second.get("/api/backup/usb/jobs/job-1").status_code == 200
    assert TestClient(app).get("/api/backup/usb/current").status_code == 401


def test_usb_current_reports_no_job(monkeypatch) -> None:
    monkeypatch.setattr(main, "_media_request", lambda _payload: {"job": None})
    logged_in, _ = _logged_in_client(monkeypatch)
    assert logged_in.get("/api/backup/usb/current").json() == {"job": None}


def test_usb_download_requires_the_token_of_the_own_session(monkeypatch) -> None:
    monkeypatch.setattr(main, "sessions", {})
    monkeypatch.setattr(main, "usb_jobs", {})
    first, _ = _logged_in_client(monkeypatch)
    second, _ = _logged_in_client(monkeypatch)
    first_session = first.cookies.get("kibm_session")
    main.usb_jobs["job-1"] = {first_session: "first-token"}
    assert second.get("/api/backup/usb/download/job-1/a.img?token=first-token").status_code == 404


NAS_POST_ROUTES = ["discover", "shares", "check", "browse", "mkdir", "save", "delete", "images", "start"]
ORIGIN = {"Origin": "http://testserver"}


def _fake_worker(monkeypatch, replies=None):
    sent = []

    def fake(payload, timeout=15):
        sent.append(payload)
        if payload["action"] == "update_status":
            return {"available": True, "state": "idle"}
        return (replies or {}).get(payload["action"], {})

    monkeypatch.setattr(main, "_media_request", fake)
    return sent


def test_nas_get_without_session_reveals_only_configured(monkeypatch) -> None:
    _fake_worker(monkeypatch, {"nas_get": {"configured": True, "server": "h", "share": "S", "username": "u", "folder": "f"}})
    assert TestClient(app).get("/api/backup/nas").json() == {"configured": True}
    logged_in, _ = _logged_in_client(monkeypatch)
    assert logged_in.get("/api/backup/nas").json()["server"] == "h"


def test_nas_post_routes_require_session_and_csrf(monkeypatch) -> None:
    sent = _fake_worker(monkeypatch)
    anonymous = TestClient(app)
    logged_in, _ = _logged_in_client(monkeypatch)
    for route in NAS_POST_ROUTES:
        assert anonymous.post(f"/api/backup/nas/{route}", json={"confirmed": True}, headers=ORIGIN).status_code == 401, route
        assert logged_in.post(f"/api/backup/nas/{route}", json={"confirmed": True}, headers=ORIGIN).status_code == 403, route
    assert not [payload for payload in sent if payload["action"].startswith("nas_")]


def test_nas_save_rejects_invalid_input_before_worker(monkeypatch) -> None:
    sent = _fake_worker(monkeypatch)
    logged_in, csrf = _logged_in_client(monkeypatch)
    response = logged_in.post(
        "/api/backup/nas/save", json={"server": "h", "share": "a/b", "username": "u", "password": "p"},
        headers={**ORIGIN, "X-KIBM-CSRF": csrf},
    )
    assert response.status_code == 400
    assert not [payload for payload in sent if payload["action"].startswith("nas_")]


def test_nas_start_requires_confirmation_and_preflight(monkeypatch) -> None:
    job = {"job_id": "j", "state": "wird vorbereitet", "files": [], "target": "nas"}
    sent = _fake_worker(monkeypatch, {"nas_start": {"job": job, "device": {"path": "//h/S/f", "model": "NAS (SMB)", "size_bytes": 0}}})
    logged_in, csrf = _logged_in_client(monkeypatch)
    headers = {**ORIGIN, "X-KIBM-CSRF": csrf}
    assert logged_in.post("/api/backup/nas/start", json={"confirmed": False}, headers=headers).status_code == 400
    monkeypatch.setattr(services, "backup_preflight", lambda: {"backup_available": False, "blockers": ["Druck läuft"]})
    assert logged_in.post("/api/backup/nas/start", json={"confirmed": True}, headers=headers).status_code == 409
    monkeypatch.setattr(services, "backup_preflight", lambda: {"backup_available": True, "blockers": []})
    response = logged_in.post("/api/backup/nas/start", json={"confirmed": True, "shrink": True}, headers=headers)
    assert response.status_code == 200
    assert response.json()["device"]["model"] == "NAS (SMB)" and response.json()["download_token"]
    assert sent[-1] == {"action": "nas_start", "shrink": True, "keep_full": False, "verify_image": True, "confirmed": True}


def test_nas_save_forwards_password_but_never_returns_it(monkeypatch) -> None:
    sent = _fake_worker(monkeypatch, {"nas_save": {"saved": True, "display": "//h/S/f"}})
    logged_in, csrf = _logged_in_client(monkeypatch)
    response = logged_in.post(
        "/api/backup/nas/save", json={"server": "h", "share": "S", "username": "u", "password": "geheim", "folder": "f"},
        headers={**ORIGIN, "X-KIBM-CSRF": csrf},
    )
    assert response.status_code == 200 and "geheim" not in response.text
    assert sent[-1]["password"] == "geheim" and sent[-1]["action"] == "nas_save"


def test_nas_worker_errors_become_conflicts(monkeypatch) -> None:
    def fake(payload, timeout=15):
        raise ValueError("NAS nicht erreichbar.")

    monkeypatch.setattr(main, "_media_request", fake)
    logged_in, csrf = _logged_in_client(monkeypatch)
    response = logged_in.post("/api/backup/nas/browse", json={}, headers={**ORIGIN, "X-KIBM-CSRF": csrf})
    assert response.status_code == 409 and response.json()["detail"] == "NAS nicht erreichbar."


def test_nas_routes_run_in_the_threadpool() -> None:
    import inspect

    routes = {route.path: route for route in app.routes if getattr(route, "path", "").startswith("/api/backup/nas")}
    assert set(routes) == {"/api/backup/nas"} | {f"/api/backup/nas/{name}" for name in NAS_POST_ROUTES}
    assert not any(inspect.iscoroutinefunction(route.endpoint) for route in routes.values())


def test_nas_timeout_gets_a_readable_message(monkeypatch) -> None:
    def fake(payload, timeout=15):
        assert timeout >= 120
        raise TimeoutError("timed out")

    monkeypatch.setattr(main, "_media_request", fake)
    logged_in, csrf = _logged_in_client(monkeypatch)
    response = logged_in.post("/api/backup/nas/images", json={}, headers={**ORIGIN, "X-KIBM-CSRF": csrf})
    assert response.status_code == 409
    assert response.json()["detail"] == "Das NAS hat zu lange nicht geantwortet. Prüfe den Status mit „Erneut prüfen“."


def test_usb_cancel_needs_csrf_and_the_own_job(monkeypatch) -> None:
    calls = []
    job = {"job_id": "job-1", "state": "sichern", "cancel_requested": True}
    monkeypatch.setattr(main, "sessions", {})
    monkeypatch.setattr(main, "usb_jobs", {})
    monkeypatch.setattr(main, "_media_request", lambda payload: calls.append(payload) or {"job": job})
    logged_in, csrf = _logged_in_client(monkeypatch)
    url = "/api/backup/usb/jobs/job-1/cancel"

    assert logged_in.post(url, headers={**ORIGIN, "X-KIBM-CSRF": csrf}).status_code == 404
    logged_in.get("/api/backup/usb/current")
    assert logged_in.post(url, headers=ORIGIN).status_code == 403
    response = logged_in.post(url, headers={**ORIGIN, "X-KIBM-CSRF": csrf})
    assert response.status_code == 200 and response.json()["cancel_requested"] is True
    assert {"action": "cancel", "job_id": "job-1"} in calls
