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
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_backup": True})

    assert b"".join(image_stream.stream_job(job)) == source
    assert job.state == "übertragen"
    assert job.sent_bytes == len(source)
    assert job.sha256 == hashlib.sha256(source).hexdigest()


def test_stream_job_aborts_when_printer_is_not_standby(monkeypatch) -> None:
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
    monkeypatch.setattr(services, "klipper_status", lambda: {"safe_to_backup": False})

    try:
        next(image_stream.stream_job(job))
    except OSError as error:
        assert "Druckerstatus" in str(error)
    else:
        raise AssertionError("The transfer must abort when the printer is not in standby.")
    assert job.state == "fehler"
    assert job.sent_bytes == 0
