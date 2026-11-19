import gzip
import hashlib
import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from kibm.recovery import create_image_tryboot_plan, create_tryboot_plan, validate_initramfs


def _cpio_entry(name: str, content: bytes, mode: int, inode: int) -> bytes:
    name_bytes = name.encode() + b"\0"
    fields = [inode, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name_bytes), 0]
    header = b"070701" + b"".join(f"{field:08x}".encode() for field in fields)
    result = bytearray(header + name_bytes)
    result.extend(b"\0" * (-len(result) % 4))
    result.extend(content)
    result.extend(b"\0" * (-len(result) % 4))
    return bytes(result)


def _initramfs(tmp_path: Path, *, include_init: bool = True, executable: bool = True) -> Path:
    archive = bytearray()
    if include_init:
        mode = stat.S_IFREG | (0o755 if executable else 0o644)
        archive.extend(_cpio_entry("init", b"#!/bin/sh\nexec sh\n", mode, 1))
    archive.extend(_cpio_entry("TRAILER!!!", b"", 0, 2))
    path = tmp_path / "recovery.cpio.gz"
    path.write_bytes(gzip.compress(bytes(archive)))
    return path


def _boot_dir(tmp_path: Path) -> Path:
    boot = tmp_path / "boot"
    boot.mkdir(parents=True)
    (boot / "config.txt").write_text("[all]\ndtoverlay=vc4-kms-v3d\n", encoding="utf-8")
    (boot / "cmdline.txt").write_text("root=PARTUUID=abcd-02 rootwait quiet\n", encoding="ascii")
    return boot


def test_validate_initramfs_requires_executable_init(tmp_path: Path) -> None:
    validate_initramfs(_initramfs(tmp_path))

    with pytest.raises(ValueError, match="keine ausführbare Datei /init"):
        validate_initramfs(_initramfs(tmp_path, include_init=False))
    with pytest.raises(ValueError, match="ausführbare"):
        validate_initramfs(_initramfs(tmp_path, executable=False))


def test_validate_initramfs_requires_one_complete_archive(tmp_path: Path) -> None:
    mode = stat.S_IFREG | 0o755
    archive = _cpio_entry("init", b"#!/bin/sh\n", mode, 1)
    path = tmp_path / "incomplete.cpio.gz"
    path.write_bytes(gzip.compress(archive))
    with pytest.raises(ValueError, match="keinen vollständigen CPIO-Abschluss"):
        validate_initramfs(path)

    archive += _cpio_entry("TRAILER!!!", b"", 0, 2)
    archive += _cpio_entry("later", b"hidden archive", stat.S_IFREG | 0o644, 3)
    path.write_bytes(gzip.compress(archive))
    with pytest.raises(ValueError, match="zusätzliche Initramfs-Daten"):
        validate_initramfs(path)

    duplicate = _cpio_entry("init", b"#!/bin/sh\n", mode, 1)
    duplicate += _cpio_entry("./init", b"#!/bin/sh\n", mode, 2)
    duplicate += _cpio_entry("TRAILER!!!", b"", 0, 3)
    path.write_bytes(gzip.compress(duplicate))
    with pytest.raises(ValueError, match="mehrere /init-Dateien"):
        validate_initramfs(path)


def test_plan_bundle_never_changes_boot_directory(tmp_path: Path) -> None:
    boot = _boot_dir(tmp_path)
    config_before = (boot / "config.txt").read_bytes()
    cmdline_before = (boot / "cmdline.txt").read_bytes()
    initramfs = _initramfs(tmp_path)
    output = tmp_path / "plan"

    create_tryboot_plan(boot, initramfs, output)

    assert (boot / "config.txt").read_bytes() == config_before
    assert (boot / "cmdline.txt").read_bytes() == cmdline_before
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "plan-only"
    assert manifest["boot_directory_modified"] is False
    assert "root=" not in (output / "candidate" / "kibm-recovery.cmdline").read_text()
    candidate = (output / "candidate" / "tryboot.txt").read_text(encoding="utf-8")
    assert candidate.index("include config.txt") < candidate.index("[all]")
    assert "auto_initramfs=0" in candidate
    assert "cmdline=kibm-recovery.cmdline" in candidate
    assert "initramfs kibm-recovery.cpio.gz followkernel" in candidate
    assert (output / "candidate" / "kibm-recovery.cpio.gz").read_bytes() == initramfs.read_bytes()


@pytest.mark.parametrize("existing_file", ["autoboot.txt", "tryboot.txt"])
def test_plan_refuses_existing_boot_selection_files(tmp_path: Path, existing_file: str) -> None:
    boot = _boot_dir(tmp_path)
    (boot / existing_file).write_text("# existing boot selection\n", encoding="utf-8")
    output = tmp_path / "plan"

    with pytest.raises(ValueError, match=existing_file):
        create_tryboot_plan(boot, _initramfs(tmp_path), output)
    assert not output.exists()


def test_plan_refuses_output_inside_boot_tree_and_existing_output(tmp_path: Path) -> None:
    boot = _boot_dir(tmp_path)
    initramfs = _initramfs(tmp_path)
    with pytest.raises(ValueError, match="getrennt"):
        create_tryboot_plan(boot, initramfs, boot / "plan")

    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("leave intact", encoding="utf-8")
    with pytest.raises(ValueError, match="existiert bereits"):
        create_tryboot_plan(boot, initramfs, existing)
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "leave intact"


def test_plan_requires_regular_boot_files_and_valid_initramfs(tmp_path: Path) -> None:
    boot = _boot_dir(tmp_path)
    initramfs = _initramfs(tmp_path)
    (boot / "config.txt").unlink()
    with pytest.raises(ValueError, match="config.txt und cmdline.txt"):
        create_tryboot_plan(boot, initramfs, tmp_path / "missing-config-plan")

    boot = _boot_dir(tmp_path / "another")
    invalid = tmp_path / "invalid.gz"
    invalid.write_bytes(gzip.compress(b"not cpio"))
    with pytest.raises(ValueError, match="CPIO-newc"):
        create_tryboot_plan(boot, invalid, tmp_path / "invalid-plan")


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("sfdisk", "blkid", "mkfs.fat", "mcopy", "mdir", "mtype")),
    reason="Image integration requires util-linux and mtools",
)
def test_plan_image_reads_fat_image_without_modifying_it(tmp_path: Path) -> None:
    image = tmp_path / "klipper.img"
    with image.open("wb") as target:
        target.truncate(64 * 1024 * 1024)
    partition_table = b"label: dos\nunit: sectors\nsector-size: 512\n\nstart=2048, size=120000, type=c, bootable\n"
    subprocess.run(["sfdisk", str(image)], input=partition_table, check=True, capture_output=True)
    subprocess.run(["mkfs.fat", "-F", "32", "--offset=2048", str(image)], check=True, capture_output=True)
    config = tmp_path / "config.txt"
    cmdline = tmp_path / "cmdline.txt"
    config.write_bytes(b"[all]\r\narm_64bit=1\r\n")
    cmdline.write_bytes(b"root=PARTUUID=abcd-02 rootwait quiet\n")
    image_spec = f"{image}@@1048576"
    subprocess.run(["mcopy", "-i", image_spec, str(config), "::CONFIG.TXT"], check=True, capture_output=True)
    subprocess.run(["mcopy", "-i", image_spec, str(cmdline), "::CMDLINE.TXT"], check=True, capture_output=True)
    before = hashlib.sha256(image.read_bytes()).digest()

    output = create_image_tryboot_plan(image, _initramfs(tmp_path), tmp_path / "image-plan")

    assert hashlib.sha256(image.read_bytes()).digest() == before
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "plan-only"
    assert manifest["boot_directory_modified"] is False
    assert manifest["normal_boot_files"]["config.txt"] == hashlib.sha256(config.read_bytes()).hexdigest()
