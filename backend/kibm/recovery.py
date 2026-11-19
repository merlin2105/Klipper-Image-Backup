from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

PLAN_VERSION = 1
INITRAMFS_NAME = "kibm-recovery.cpio.gz"
RECOVERY_CMDLINE = "console=serial0,115200 console=tty1 rdinit=/init panic=10"
MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_initramfs(path: Path) -> None:
    """Accept only a gzip-compressed newc archive with an executable /init."""
    if not path.is_file() or path.is_symlink():
        raise ValueError("Recovery-initramfs muss eine reguläre Datei sein.")
    if path.stat().st_size > MAX_COMPRESSED_BYTES:
        raise ValueError("Recovery-initramfs überschreitet das Größenlimit von 256 MiB.")

    unpacked = 0
    found_init = False
    found_trailer = False
    trailing_archive_data = False

    def read_exact(source, count: int) -> bytes:
        nonlocal unpacked
        value = source.read(count)
        if len(value) != count:
            raise ValueError("Initramfs enthält einen abgeschnittenen CPIO-Eintrag.")
        unpacked += count
        if unpacked > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Entpacktes Initramfs überschreitet 512 MiB.")
        return value

    def skip_exact(source, count: int) -> None:
        while count:
            chunk = read_exact(source, min(count, 1024 * 1024))
            count -= len(chunk)

    def align(source) -> None:
        padding = (-unpacked) % 4
        if padding:
            read_exact(source, padding)

    try:
        with gzip.open(path, "rb") as source:
            while True:
                magic = source.read(6)
                if not magic:
                    break
                unpacked += len(magic)
                if unpacked > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Entpacktes Initramfs überschreitet 512 MiB.")
                if magic not in (b"070701", b"070702"):
                    raise ValueError("Initramfs muss ein gzip-komprimiertes CPIO-newc-Archiv sein.")
                header = read_exact(source, 104)
                try:
                    mode = int(header[8:16], 16)
                    file_size = int(header[48:56], 16)
                    name_size = int(header[88:96], 16)
                except ValueError as error:
                    raise ValueError("Initramfs enthält ungültige CPIO-Metadaten.") from error
                if not 1 <= name_size <= 4096:
                    raise ValueError("Initramfs enthält einen ungültigen CPIO-Dateinamen.")
                raw_name = read_exact(source, name_size)
                align(source)
                if not raw_name.endswith(b"\0"):
                    raise ValueError("Initramfs enthält einen ungültigen CPIO-Dateinamen.")
                try:
                    name = raw_name[:-1].decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("Initramfs enthält einen nicht lesbaren Dateinamen.") from error
                if name.startswith("/") or ".." in Path(name).parts:
                    raise ValueError("Initramfs enthält einen unsicheren Dateipfad.")
                if name == "TRAILER!!!":
                    if file_size != 0:
                        raise ValueError("CPIO-Abschluss hat unerwartete Nutzdaten.")
                    found_trailer = True
                    while chunk := source.read(1024 * 1024):
                        unpacked += len(chunk)
                        if unpacked > MAX_UNCOMPRESSED_BYTES:
                            raise ValueError("Entpacktes Initramfs überschreitet 512 MiB.")
                        trailing_archive_data |= any(chunk)
                    break
                if name in ("init", "./init"):
                    if found_init:
                        raise ValueError("Initramfs enthält mehrere /init-Dateien.")
                    if not stat.S_ISREG(mode) or not mode & 0o111 or file_size == 0:
                        raise ValueError("/init muss eine ausführbare, nicht leere reguläre Datei sein.")
                    found_init = True
                skip_exact(source, file_size)
                align(source)
    except (OSError, EOFError) as error:
        raise ValueError(f"Recovery-initramfs konnte nicht gelesen werden: {error}") from error

    if not found_init:
        raise ValueError("Initramfs enthält keine ausführbare Datei /init.")
    if not found_trailer:
        raise ValueError("Initramfs enthält keinen vollständigen CPIO-Abschluss.")
    if trailing_archive_data:
        raise ValueError("Mehrere oder zusätzliche Initramfs-Daten nach dem CPIO-Abschluss werden nicht unterstützt.")


def create_tryboot_plan(boot_dir: Path, initramfs: Path, output_dir: Path) -> Path:
    """Create an inspectable bundle without writing to the supplied boot directory."""
    boot_dir = boot_dir.resolve(strict=True)
    if initramfs.is_symlink():
        raise ValueError("Recovery-initramfs darf kein symbolischer Link sein.")
    initramfs = initramfs.resolve(strict=True)
    output_dir = output_dir.resolve()
    if not boot_dir.is_dir():
        raise ValueError("Boot-Verzeichnis ist kein Verzeichnis.")
    if output_dir == boot_dir or output_dir.is_relative_to(boot_dir) or boot_dir.is_relative_to(output_dir):
        raise ValueError("Plan-Ausgabe muss getrennt vom Boot-Verzeichnis liegen.")
    if output_dir.exists():
        raise ValueError("Plan-Ausgabe existiert bereits; vorhandene Dateien werden nicht überschrieben.")
    if not output_dir.parent.is_dir():
        raise ValueError("Übergeordnetes Verzeichnis der Plan-Ausgabe existiert nicht.")

    config = boot_dir / "config.txt"
    cmdline = boot_dir / "cmdline.txt"
    if not config.is_file() or config.is_symlink() or not cmdline.is_file() or cmdline.is_symlink():
        raise ValueError("Erwartet werden reguläre config.txt und cmdline.txt im Boot-Verzeichnis.")
    if (boot_dir / "autoboot.txt").exists() or (boot_dir / "autoboot.txt").is_symlink():
        raise ValueError("autoboot.txt wird nicht unterstützt; der Bootpfad bleibt unverändert.")
    if (boot_dir / "tryboot.txt").exists() or (boot_dir / "tryboot.txt").is_symlink():
        raise ValueError("tryboot.txt existiert bereits; der Plan würde mit einer vorhandenen Konfiguration kollidieren.")
    validate_initramfs(initramfs)

    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        (stage / "candidate").mkdir()
        (stage / "candidate" / "tryboot.txt").write_text(
            "# KIBM plan only; inspect and hardware-test before installation.\n"
            "include config.txt\n"
            "[all]\n"
            "auto_initramfs=0\n"
            "cmdline=kibm-recovery.cmdline\n"
            f"initramfs {INITRAMFS_NAME} followkernel\n",
            encoding="utf-8",
        )
        (stage / "candidate" / "kibm-recovery.cmdline").write_text(RECOVERY_CMDLINE + "\n", encoding="ascii")
        shutil.copyfile(initramfs, stage / "candidate" / INITRAMFS_NAME)
        manifest = {
            "plan_version": PLAN_VERSION,
            "mode": "plan-only",
            "boot_directory_modified": False,
            "normal_boot_files": {
                "config.txt": _sha256(config),
                "cmdline.txt": _sha256(cmdline),
            },
            "candidate_files": {
                "tryboot.txt": _sha256(stage / "candidate" / "tryboot.txt"),
                "kibm-recovery.cmdline": _sha256(stage / "candidate" / "kibm-recovery.cmdline"),
                INITRAMFS_NAME: _sha256(stage / "candidate" / INITRAMFS_NAME),
            },
            "limitations": [
                "This bundle does not install or activate a boot configuration.",
                "The recovery initramfs must be built for the target Pi kernel and verified on hardware.",
                "The one-shot tryboot fallback does not prove that recovery networking or return works.",
                "Secure Boot is unsupported by this candidate and must be confirmed disabled.",
                "Pi 4 Model B revisions 1.0 and 1.1 require EEPROM write protection to be disabled for tryboot.",
            ],
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="KIBM: read-only Raspberry Pi tryboot plan builder")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Create an inspectable candidate bundle; never changes the boot directory")
    plan.add_argument("--boot-dir", type=Path, required=True)
    plan.add_argument("--initramfs", type=Path, required=True)
    plan.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = create_tryboot_plan(arguments.boot_dir, arguments.initramfs, arguments.output_dir)
    except (OSError, ValueError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"plan_directory": str(result), "boot_directory_modified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
