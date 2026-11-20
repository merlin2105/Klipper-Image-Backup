import hashlib
import stat

from kibm import image_stream, services


def test_stream_job_emits_full_source_and_records_sha256(monkeypatch) -> None:
    source = b"raw-card-image-data"
    job = image_stream.ImageJob(
        job_id="job",
        session_id="session",
        download_token="token",
        source_path="/dev/mmcblk0",
        source_device=123,
        size_bytes=len(source),
        filename="printer-sdkarte.img",
    )
    monkeypatch.setattr(image_stream.os, "open", lambda *args, **kwargs: 7)
    monkeypatch.setattr(image_stream.os, "fstat", lambda _fd: type("BlockInfo", (), {"st_mode": stat.S_IFBLK, "st_rdev": 123})())
    monkeypatch.setattr(image_stream, "_device_size", lambda _fd: len(source))
    monkeypatch.setattr(image_stream.os, "read", lambda _fd, amount: source[:amount])
    monkeypatch.setattr(image_stream.os, "close", lambda _fd: None)
    monkeypatch.setattr(services, "klipper_status", lambda: {"classification": "ready"})

    assert b"".join(image_stream.stream_job(job)) == source
    assert job.state == "übertragen"
    assert job.sent_bytes == len(source)
    assert job.sha256 == hashlib.sha256(source).hexdigest()


def test_stream_job_aborts_during_active_print(monkeypatch) -> None:
    job = image_stream.ImageJob(
        job_id="job",
        session_id="session",
        download_token="token",
        source_path="/dev/mmcblk0",
        source_device=123,
        size_bytes=8,
        filename="printer-sdkarte.img",
    )
    monkeypatch.setattr(image_stream.os, "open", lambda *args, **kwargs: 7)
    monkeypatch.setattr(image_stream.os, "fstat", lambda _fd: type("BlockInfo", (), {"st_mode": stat.S_IFBLK, "st_rdev": 123})())
    monkeypatch.setattr(image_stream, "_device_size", lambda _fd: 8)
    monkeypatch.setattr(image_stream.os, "close", lambda _fd: None)
    monkeypatch.setattr(services, "klipper_status", lambda: {"classification": "active"})

    try:
        next(image_stream.stream_job(job))
    except OSError as error:
        assert "aktiven Druck" in str(error)
    else:
        raise AssertionError("The transfer must abort when a print is active.")
    assert job.state == "fehler"
    assert job.sent_bytes == 0


def _fake_card(monkeypatch, size: int = 8) -> dict:
    monkeypatch.setattr(image_stream, "_jobs", {})
    monkeypatch.setattr(image_stream.os, "open", lambda *args, **kwargs: 7)
    monkeypatch.setattr(image_stream.os, "fstat", lambda _fd: type("BlockInfo", (), {"st_mode": stat.S_IFBLK, "st_rdev": 123})())
    monkeypatch.setattr(image_stream, "_device_size", lambda _fd: size)
    monkeypatch.setattr(image_stream.os, "close", lambda _fd: None)
    return {"path": "/dev/mmcblk0", "type": "disk", "size_bytes": size}


def test_unclaimed_job_blocks_only_until_it_expires(monkeypatch) -> None:
    device = _fake_card(monkeypatch)
    created = image_stream.create_job("session", device)
    assert image_stream.has_active_job()
    try:
        image_stream.create_job("session", device)
    except ValueError:
        pass
    else:
        raise AssertionError("A fresh unclaimed job must still block a second job.")

    image_stream._jobs[created["job_id"]].created_at -= image_stream.UNCLAIMED_TTL_SECONDS + 1

    assert not image_stream.has_active_job()
    status = image_stream.job_status(created["job_id"], "session")
    assert status["state"] == "abgelaufen"
    assert status["error"]
    assert image_stream.create_job("session", device)["job_id"] != created["job_id"]


def test_expired_job_cannot_be_downloaded(monkeypatch) -> None:
    device = _fake_card(monkeypatch)
    created = image_stream.create_job("session", device)
    image_stream._jobs[created["job_id"]].created_at -= image_stream.UNCLAIMED_TTL_SECONDS + 1
    token = created["download_url"].split("token=", 1)[1]
    try:
        image_stream.claim_download(created["job_id"], "session", token)
    except ValueError:
        pass
    else:
        raise AssertionError("An expired job must not be downloadable.")


def test_stream_job_refuses_job_that_is_no_longer_ready(monkeypatch) -> None:
    _fake_card(monkeypatch)
    job = image_stream.ImageJob(
        job_id="job", session_id="session", download_token="token", source_path="/dev/mmcblk0",
        source_device=123, size_bytes=8, filename="printer-sdkarte.img", state="abgelaufen",
    )
    try:
        next(image_stream.stream_job(job))
    except OSError:
        pass
    else:
        raise AssertionError("Only a ready job may start streaming.")
    assert job.state == "abgelaufen"
