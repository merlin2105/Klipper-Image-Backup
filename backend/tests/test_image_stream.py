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
        device_bytes=len(source),
        filename="printer-sdkarte.img",
    )
    monkeypatch.setattr(image_stream.os, "sync", lambda: None)
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
        device_bytes=8,
        filename="printer-sdkarte.img",
    )
    monkeypatch.setattr(image_stream.os, "sync", lambda: None)
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


def _card_mbr(end_sector: int) -> bytes:
    import struct
    mbr = bytearray(512)
    mbr[446 + 4] = 0x83
    struct.pack_into("<II", mbr, 446 + 8, 1, end_sector - 1)
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


def _fake_card(monkeypatch, size: int = 8 * 512, partition_end_sector: int = 8) -> dict:
    monkeypatch.setattr(image_stream, "_jobs", {})
    monkeypatch.setattr(image_stream.os, "pread", lambda _fd, _count, _offset: _card_mbr(partition_end_sector))
    monkeypatch.setattr(image_stream.os, "sync", lambda: None)
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
        source_device=123, size_bytes=8, device_bytes=8, filename="printer-sdkarte.img", state="abgelaufen",
    )
    try:
        next(image_stream.stream_job(job))
    except OSError:
        pass
    else:
        raise AssertionError("Only a ready job may start streaming.")
    assert job.state == "abgelaufen"


def test_image_ends_at_the_last_partition_not_at_the_end_of_the_card(monkeypatch) -> None:
    # A card larger than the partitions must not produce an image that is too big
    # for a card of the original size.
    device = _fake_card(monkeypatch, size=16 * 512, partition_end_sector=8)
    created = image_stream.create_job("session", device)
    job = image_stream._jobs[created["job_id"]]
    assert created["size_bytes"] == 8 * 512
    assert job.device_bytes == 16 * 512


def test_stream_syncs_and_reads_only_up_to_the_partition_end(monkeypatch) -> None:
    calls = []
    source = bytes(range(256)) * 64  # 16 sectors
    job = image_stream.ImageJob(
        job_id="job", session_id="session", download_token="token", source_path="/dev/mmcblk0",
        source_device=123, size_bytes=8 * 512, device_bytes=len(source), filename="printer-sdkarte.img",
    )
    position = [0]

    def read(_fd, amount):
        chunk = source[position[0]:position[0] + amount]
        position[0] += len(chunk)
        return chunk

    monkeypatch.setattr(image_stream.os, "sync", lambda: calls.append("sync"))
    monkeypatch.setattr(image_stream.os, "open", lambda *args, **kwargs: calls.append("open") or 7)
    monkeypatch.setattr(image_stream.os, "fstat", lambda _fd: type("BlockInfo", (), {"st_mode": stat.S_IFBLK, "st_rdev": 123})())
    monkeypatch.setattr(image_stream, "_device_size", lambda _fd: len(source))
    monkeypatch.setattr(image_stream.os, "read", read)
    monkeypatch.setattr(image_stream.os, "close", lambda _fd: None)
    monkeypatch.setattr(services, "klipper_status", lambda: {"classification": "ready"})

    assert b"".join(image_stream.stream_job(job)) == source[:8 * 512]
    assert calls[0] == "sync"
    assert job.state == "übertragen"
