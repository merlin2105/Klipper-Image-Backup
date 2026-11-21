import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kibm import media_worker


def _handler(monkeypatch, tmp_path, run):
    replies = []
    monkeypatch.setattr(media_worker, "_job", None)
    monkeypatch.setattr(media_worker, "VOLUME_PATH", tmp_path / "volume")
    monkeypatch.setattr(media_worker, "UPDATE_COMMIT_PATH", tmp_path / "update-commit")
    monkeypatch.setattr(media_worker, "_run", run)
    handler = media_worker.MediaHandler.__new__(media_worker.MediaHandler)
    handler._reply = replies.append
    return handler, replies


def test_update_start_records_confirmed_commit_before_starting_unit(monkeypatch, tmp_path) -> None:
    commands = []

    def run(args, timeout=30):
        commands.append(args)
        if args[:2] == ["systemctl", "start"]:
            assert (tmp_path / "update-commit").read_text() == "b" * 40 + "\n"
        return SimpleNamespace(stdout="inactive\n")

    handler, replies = _handler(monkeypatch, tmp_path, run)
    handler._update_start({"action": "update_start", "commit": "b" * 40})

    assert ["systemctl", "start", "--no-block", "kibm-update.service"] in commands
    assert (tmp_path / "update-commit").stat().st_mode & 0o777 == 0o600
    assert replies == [{"state": "running"}]


@pytest.mark.parametrize("commit", [None, "", "B" * 40, "b" * 39, "b" * 40 + "\n", "../../etc/passwd"])
def test_update_start_rejects_missing_or_invalid_commit(monkeypatch, tmp_path, commit) -> None:
    commands = []
    handler, _ = _handler(monkeypatch, tmp_path, lambda args, timeout=30: commands.append(args) or SimpleNamespace(stdout="inactive\n"))

    with pytest.raises(ValueError):
        handler._update_start({"action": "update_start", "commit": commit})
    assert not any(args[:2] == ["systemctl", "start"] for args in commands)
    assert not (tmp_path / "update-commit").exists()


def test_update_start_removes_commit_when_unit_cannot_start(monkeypatch, tmp_path) -> None:
    def run(args, timeout=30):
        if args[:2] == ["systemctl", "start"]:
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(stdout="inactive\n")

    handler, _ = _handler(monkeypatch, tmp_path, run)
    with pytest.raises(subprocess.CalledProcessError):
        handler._update_start({"action": "update_start", "commit": "c" * 40})
    assert not (tmp_path / "update-commit").exists()


class _FakeConnection:
    def getsockopt(self, *_args):
        import struct
        return struct.pack("3i", 1, 4242, 4242)


def _socket_handler(monkeypatch, raw: bytes):
    import io
    monkeypatch.setattr(media_worker, "_worker_uid", 4242)
    handler = media_worker.MediaHandler.__new__(media_worker.MediaHandler)
    handler.request = _FakeConnection()
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    return handler


@pytest.mark.parametrize("raw", [b"[]\n", b'"devices"\n', b"42\n"])
def test_non_object_requests_get_an_error_reply(monkeypatch, raw) -> None:
    import json
    handler = _socket_handler(monkeypatch, raw)
    handler.handle()
    assert json.loads(handler.wfile.getvalue()) == {"error": "invalid_request"}


def test_current_reports_the_running_job(monkeypatch) -> None:
    import json
    job = media_worker.MediaJob(
        job_id="job-1", state="sichern", phase="sichern", message="läuft", image_size=10,
        source_device="/dev/mmcblk0", shrink=False, keep_full=False,
    )
    monkeypatch.setattr(media_worker, "_job", job)
    handler = _socket_handler(monkeypatch, b'{"action":"current"}\n')
    handler.handle()
    assert json.loads(handler.wfile.getvalue())["job"]["job_id"] == "job-1"


# --- USB image list cache -------------------------------------------------

def _volume(identity=("/dev/sda1", "uuid", 100, "7")):
    return {"identity": identity, "path": identity[0], "device": {"path": "/dev/sda"}}


def _scan_counter(monkeypatch, volumes):
    scans = []
    monkeypatch.setattr(media_worker, "_job", None)
    monkeypatch.setattr(media_worker, "_images_cache", None)
    monkeypatch.setattr(media_worker, "_image_volumes", lambda: volumes[0])
    monkeypatch.setattr(media_worker, "_existing_images", lambda vols: scans.append(vols) or [])
    handler = media_worker.MediaHandler.__new__(media_worker.MediaHandler)
    handler._reply = lambda _result: None
    return handler, scans


def test_image_list_is_cached_until_stick_or_files_change(monkeypatch) -> None:
    volumes = [[_volume()]]
    handler, scans = _scan_counter(monkeypatch, volumes)

    handler._list_images()
    handler._list_images()
    assert len(scans) == 1

    media_worker._invalidate_images()
    handler._list_images()
    assert len(scans) == 2

    volumes[0] = [_volume(("/dev/sda1", "uuid", 100, "8"))]  # stick re-plugged
    handler._list_images()
    assert len(scans) == 3


def test_status_does_not_wait_for_a_running_scan(monkeypatch) -> None:
    import io
    import json
    import threading

    monkeypatch.setattr(media_worker, "_job", None)
    acquired = media_worker._scan_lock.acquire(timeout=1)
    assert acquired
    try:
        result = {}

        def ask_current():
            handler = _socket_handler(monkeypatch, b'{"action":"current"}\n')
            handler.handle()
            result["reply"] = json.loads(handler.wfile.getvalue())

        thread = threading.Thread(target=ask_current)
        thread.start()
        thread.join(timeout=1)
        assert result.get("reply") == {"job": None}
    finally:
        media_worker._scan_lock.release()


# --- pure helpers ---------------------------------------------------------

def _flat(path, *, type_="disk", transport="usb", read_only=False, mountpoints=(), parent=None):
    return {"path": path, "type": type_, "transport": transport, "read_only": read_only,
            "mountpoints": list(mountpoints), "parent": parent}


def test_devices_lists_only_writable_usb_disks_that_are_not_the_system() -> None:
    flat = [
        _flat("/dev/mmcblk0", transport=None),
        _flat("/dev/mmcblk0p2", type_="part", transport=None, mountpoints=["/"], parent="/dev/mmcblk0"),
        _flat("/dev/sda"),
        _flat("/dev/sdb", read_only=True),
        _flat("/dev/sdc", transport="sata"),
        _flat("/dev/sda1", type_="part", parent="/dev/sda"),
    ]
    assert [item["path"] for item in media_worker._devices(flat)] == ["/dev/sda"]


def test_devices_never_offers_a_usb_disk_that_holds_the_root_filesystem() -> None:
    flat = [
        _flat("/dev/sda"),
        _flat("/dev/sda2", type_="part", mountpoints=["/"], parent="/dev/sda"),
        _flat("/dev/sdb"),
    ]
    assert [item["path"] for item in media_worker._devices(flat)] == ["/dev/sdb"]


@pytest.mark.parametrize(("disk", "partition"), [
    ("/dev/sda", "/dev/sda1"), ("/dev/nvme0n1", "/dev/nvme0n1p1"), ("/dev/mmcblk1", "/dev/mmcblk1p1"),
])
def test_partition_path(disk, partition) -> None:
    assert media_worker._partition_path(disk) == partition


def test_usb_capacity_counts_two_images_when_shrinking() -> None:
    gib = 1024 ** 3
    headroom = media_worker.USB_FORMAT_HEADROOM_BYTES
    media_worker._check_usb_capacity({"size_bytes": 8 * gib + headroom}, 8 * gib, False)
    with pytest.raises(ValueError):
        media_worker._check_usb_capacity({"size_bytes": 8 * gib + headroom - 1}, 8 * gib, False)
    with pytest.raises(ValueError):
        media_worker._check_usb_capacity({"size_bytes": 16 * gib}, 8 * gib, True)
    media_worker._check_usb_capacity({"size_bytes": 16 * gib + headroom}, 8 * gib, True)


# --- read-only image inspection -------------------------------------------

def _synthetic_image(path, *, fs_blocks, free_blocks, partition_sectors, image_bytes, magic=0xEF53):
    import struct
    start_sector = 2048
    mbr = bytearray(512)
    struct.pack_into("<I", mbr, 446 + 8, start_sector)
    struct.pack_into("<I", mbr, 446 + 12, partition_sectors)
    mbr[510:512] = b"\x55\xaa"
    superblock = bytearray(1024)
    struct.pack_into("<I", superblock, 4, fs_blocks)
    struct.pack_into("<I", superblock, 12, free_blocks)
    struct.pack_into("<I", superblock, 24, 2)  # 4096-byte blocks
    struct.pack_into("<H", superblock, 56, magic)
    with path.open("wb") as handle:  # sparse: only the headers use disk space
        handle.truncate(image_bytes)
        handle.write(mbr)
        handle.seek(start_sector * 512 + 1024)
        handle.write(superblock)
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def test_inspect_image_reports_shrinkable_space(tmp_path) -> None:
    # 1 GiB filesystem in a 1 GiB partition, half of it free.
    image = tmp_path / "card.img"
    blocks = 262144
    size, mtime = _synthetic_image(
        image, fs_blocks=blocks, free_blocks=blocks // 2,
        partition_sectors=blocks * 8, image_bytes=2048 * 512 + blocks * 4096,
    )
    result = media_worker._inspect_image(image, size, mtime)
    assert result["status"] == "shrinkable"
    assert result["estimated_savings_bytes"] == (blocks // 2 - 5000) * 4096


def test_inspect_image_reports_compact_image(tmp_path) -> None:
    image = tmp_path / "card.img"
    blocks = 262144
    size, mtime = _synthetic_image(
        image, fs_blocks=blocks, free_blocks=100,
        partition_sectors=blocks * 8, image_bytes=2048 * 512 + blocks * 4096,
    )
    assert media_worker._inspect_image(image, size, mtime)["status"] == "already_compact"


@pytest.mark.parametrize("damage", ["no_ext4", "filesystem_larger_than_partition", "changed_since_listing"])
def test_inspect_image_refuses_unknown_or_inconsistent_images(tmp_path, damage) -> None:
    image = tmp_path / "card.img"
    blocks = 262144
    size, mtime = _synthetic_image(
        image, fs_blocks=blocks * (2 if damage == "filesystem_larger_than_partition" else 1),
        free_blocks=blocks // 2, partition_sectors=blocks * 8,
        image_bytes=2048 * 512 + blocks * 4096, magic=0x1234 if damage == "no_ext4" else 0xEF53,
    )
    if damage == "changed_since_listing":
        size += 1
    assert media_worker._inspect_image(image, size, mtime) == {"status": "unknown", "estimated_savings_bytes": 0}


# --- USB capture: size, sync, repair, no partial .img files ---------------

def _source_card(tmp_path, *, partition_end_sector=64, device_sectors=96):
    import struct
    data = bytearray(bytes(range(256)) * 2 * device_sectors)
    data[0:512] = bytes(512)
    data[446 + 4] = 0x83
    struct.pack_into("<II", data, 446 + 8, 8, partition_end_sector - 8)
    data[510:512] = b"\x55\xaa"
    card = tmp_path / "mmcblk0"
    card.write_bytes(bytes(data))
    return card, bytes(data)


def _capture_env(monkeypatch, tmp_path, card, *, repair=lambda _path: False, device_bytes=None):
    volume = tmp_path / "volume"
    volume.mkdir()
    calls = []
    monkeypatch.setattr(media_worker, "SOURCE_DEVICE", str(card))
    monkeypatch.setattr(media_worker, "VOLUME_PATH", volume)
    monkeypatch.setattr(media_worker, "USB_FORMAT_HEADROOM_BYTES", 0)
    monkeypatch.setattr(media_worker.stat, "S_ISBLK", lambda _mode: True)
    monkeypatch.setattr(media_worker, "_disk_size", lambda _path: device_bytes or card.stat().st_size)
    monkeypatch.setattr(media_worker, "_printer_active", lambda: False)
    monkeypatch.setattr(media_worker, "_format_usb", lambda _device: calls.append("format"))
    monkeypatch.setattr(media_worker, "_repair_image", lambda path: calls.append("repair") or repair(path))
    monkeypatch.setattr(media_worker.os, "sync", lambda: calls.append("sync"))
    monkeypatch.setattr(
        media_worker, "_flush_root_journal",
        lambda: calls.append("flush" if not any(volume.glob(".*.part")) else "flush-too-late"),
    )
    device ={"path": "/dev/sdz", "major_minor": "8:0", "size_bytes": 10 ** 9}
    monkeypatch.setattr(media_worker, "_selected_device", lambda _path: device)
    return volume, device, calls


def _media_job(image_size, device_size, **values):
    job = media_worker.MediaJob(
        job_id="job", state="wird vorbereitet", phase="vorbereitung", message="", image_size=image_size,
        device_size=device_size, source_device="/dev/mmcblk0", shrink=False, keep_full=False, **values,
    )
    media_worker._job = job
    return job


def test_capture_size_is_the_end_of_the_last_partition(tmp_path) -> None:
    card, data = _source_card(tmp_path, partition_end_sector=64, device_sectors=96)
    assert media_worker._capture_size(str(card), len(data)) == 64 * 512


def test_usb_capture_writes_only_the_partitions_and_leaves_no_partial_file(monkeypatch, tmp_path) -> None:
    import hashlib
    card, data = _source_card(tmp_path)
    volume, device, calls = _capture_env(monkeypatch, tmp_path, card)
    job = _media_job(64 * 512, len(data))

    media_worker._run_job(job, device, "printer")

    assert job.state == "bereit", job.error
    images = sorted(path.name for path in volume.iterdir())
    assert len(images) == 1 and images[0].endswith(".img") and not images[0].startswith(".")
    written = (volume / images[0]).read_bytes()
    assert written == data[:64 * 512]
    assert job.files[0]["sha256"] == hashlib.sha256(written).hexdigest()
    assert calls.index("sync") < calls.index("flush") < calls.index("repair")


def test_flush_root_journal_freezes_and_thaws_the_root_filesystem(monkeypatch) -> None:
    # sync only commits to the ext4 journal; freezing writes the journal back to its
    # home blocks, so a raw copy of the card no longer depends on the journal.
    calls = []
    monkeypatch.setattr(media_worker.fcntl, "ioctl", lambda fd, request, arg=0: calls.append(request))
    media_worker._flush_root_journal()
    assert calls == [media_worker.FIFREEZE, media_worker.FITHAW]


def test_flush_root_journal_failure_stops_the_backup(monkeypatch) -> None:
    def ioctl(fd, request, arg=0):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(media_worker.fcntl, "ioctl", ioctl)
    with pytest.raises(OSError, match="Journal"):
        media_worker._flush_root_journal()


def test_repaired_image_gets_the_checksum_of_the_repaired_file(monkeypatch, tmp_path) -> None:
    import hashlib

    def repair(path):
        with path.open("r+b") as image:
            image.seek(600)
            image.write(b"repaired")
        return True

    card, data = _source_card(tmp_path)
    volume, device, _calls = _capture_env(monkeypatch, tmp_path, card, repair=repair)
    job = _media_job(64 * 512, len(data))

    media_worker._run_job(job, device, "printer")

    assert job.state == "bereit", job.error
    image = next(volume.iterdir())
    assert image.read_bytes()[600:608] == b"repaired"
    assert job.files[0]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert job.sha256 == job.files[0]["sha256"]


@pytest.mark.parametrize("failure", ["short_card", "repair_failed", "card_size_changed"])
def test_failed_capture_leaves_no_image_file(monkeypatch, tmp_path, failure) -> None:
    card, data = _source_card(tmp_path)
    repair = (lambda _path: (_ for _ in ()).throw(OSError("e2fsck 4"))) if failure == "repair_failed" else (lambda _path: False)
    volume, device, _calls = _capture_env(monkeypatch, tmp_path, card, repair=repair)
    image_size = len(data) + 512 if failure == "short_card" else 64 * 512
    device_size = len(data) + 1 if failure == "card_size_changed" else len(data)
    job = _media_job(image_size, device_size)

    media_worker._run_job(job, device, "printer")

    assert job.state == "fehler"
    assert list(volume.iterdir()) == []
    assert job.files == []


def test_short_writes_are_completed(tmp_path) -> None:
    class ShortWriter:
        def __init__(self):
            self.data = bytearray()

        def write(self, chunk):
            part = bytes(chunk[:3])
            self.data.extend(part)
            return len(part)

    writer = ShortWriter()
    media_worker._write_all(writer, b"0123456789")
    assert bytes(writer.data) == b"0123456789"


def test_repair_checks_each_linux_partition_through_a_loop_device(monkeypatch, tmp_path) -> None:
    card, _data = _source_card(tmp_path)
    commands = []

    def run(args, timeout=30):
        commands.append(args)
        return SimpleNamespace(stdout="/dev/loop7\n")

    monkeypatch.setattr(media_worker, "_run", run)
    monkeypatch.setattr(media_worker.subprocess, "run", lambda args, **_kwargs: commands.append(args) or SimpleNamespace(returncode=1, stdout="", stderr=""))

    assert media_worker._repair_image(card) is True
    assert commands[0] == ["losetup", "--find", "--show", "--offset", str(8 * 512), "--sizelimit", str(56 * 512), "--", str(card)]
    assert commands[1] == ["e2fsck", "-fy", "/dev/loop7"]
    assert commands[2] == ["losetup", "--detach", "/dev/loop7"]


@pytest.mark.parametrize(("returncode", "changed"), [(0, False), (1, True), (2, True)])
def test_repair_reports_whether_the_filesystem_changed(monkeypatch, tmp_path, returncode, changed) -> None:
    card, _data = _source_card(tmp_path)
    monkeypatch.setattr(media_worker, "_run", lambda args, timeout=30: SimpleNamespace(stdout="/dev/loop7\n"))
    monkeypatch.setattr(media_worker.subprocess, "run", lambda args, **_kwargs: SimpleNamespace(returncode=returncode, stdout="", stderr=""))
    assert media_worker._repair_image(card) is changed


def test_repair_fails_and_detaches_when_errors_remain(monkeypatch, tmp_path) -> None:
    card, _data = _source_card(tmp_path)
    commands = []
    monkeypatch.setattr(media_worker, "_run", lambda args, timeout=30: commands.append(args) or SimpleNamespace(stdout="/dev/loop7\n"))
    monkeypatch.setattr(media_worker.subprocess, "run", lambda args, **_kwargs: SimpleNamespace(returncode=4, stdout="", stderr="bad"))
    with pytest.raises(OSError):
        media_worker._repair_image(card)
    assert commands[-1] == ["losetup", "--detach", "/dev/loop7"]


# --- PiShrink ---------------------------------------------------------------

def _shrink_setup(monkeypatch, tmp_path, pishrink_output):
    volume = tmp_path / "volume"
    volume.mkdir()
    full = volume / "card.img"
    full.write_bytes(b"x" * 4096)
    commands = []

    def run(args, timeout=30):
        commands.append(args)
        Path(args[-1]).write_bytes(b"x" * 1024)
        return SimpleNamespace(stdout=pishrink_output)

    job = media_worker.MediaJob(
        job_id="job-1", state="schrumpfen", phase="kopie", message="", image_size=4096,
        source_device="/dev/mmcblk0", shrink=True, keep_full=True,
    )
    monkeypatch.setattr(media_worker, "_job", job)
    monkeypatch.setattr(media_worker, "VOLUME_PATH", volume)
    monkeypatch.setattr(media_worker, "_run", run)
    return job, full, volume / "card-geshrinkt.img", commands


def test_shrink_enables_first_boot_expansion(monkeypatch, tmp_path) -> None:
    # Without expansion the shrunk root keeps less free space than ext4 reserves
    # for root, so Moonraker and KlipperScreen fail on the restored card.
    job, full, final, commands = _shrink_setup(monkeypatch, tmp_path, "pishrink.sh: Creating new /etc/rc.local\n")

    media_worker._shrink_image(job, full, final)

    (pishrink,) = commands
    assert pishrink[0] == str(media_worker.PISHRINK_PATH)
    assert "-s" not in pishrink[1] and "o" not in pishrink[1].lstrip("-")
    assert final.stat().st_size == 1024


@pytest.mark.parametrize("output", [
    "pishrink.sh: Unable to mount loopback, autoexpand will not be enabled\n",
    "pishrink.sh: /etc not found, autoexpand will not be enabled\n",
    "WARNING: PiShrink does not yet support autoexpanding of this type of image\n",
])
def test_shrink_fails_when_first_boot_expansion_is_missing(monkeypatch, tmp_path, output) -> None:
    job, full, final, _ = _shrink_setup(monkeypatch, tmp_path, output)

    with pytest.raises(OSError):
        media_worker._shrink_image(job, full, final)
    assert not final.exists()
    assert full.exists()
