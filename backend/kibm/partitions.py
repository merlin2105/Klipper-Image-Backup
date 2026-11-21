"""MBR helpers shared by the web service and the root media worker (stdlib only)."""
from __future__ import annotations

import struct

SECTOR_BYTES = 512
GPT_PROTECTIVE = 0xEE
LINUX = 0x83


def _entries(mbr: bytes) -> list[tuple[int, int, int]]:
    if len(mbr) < SECTOR_BYTES or mbr[510:512] != b"\x55\xaa":
        raise ValueError("Die Partitionstabelle der System-microSD konnte nicht gelesen werden.")
    entries = []
    for index in range(4):
        base = 446 + index * 16
        part_type = mbr[base + 4]
        start, count = struct.unpack_from("<II", mbr, base + 8)
        if part_type and start and count:
            entries.append((part_type, start, count))
    if any(part_type == GPT_PROTECTIVE for part_type, _, _ in entries):
        raise ValueError("GPT-Partitionstabellen werden derzeit nicht unterstützt.")
    if not entries:
        raise ValueError("Auf der System-microSD wurde keine Partition gefunden.")
    return entries


def capture_bytes(mbr: bytes, device_bytes: int) -> int:
    """Bytes to image: up to the end of the last partition, like `dd` of the used layout.

    Copying the whole device makes the image larger than a card of the same nominal
    size, so it no longer fits onto the card it is meant to restore.
    """
    end = max((start + count) * SECTOR_BYTES for _, start, count in _entries(mbr))
    if end > device_bytes:
        raise ValueError("Eine Partition reicht über das Ende der System-microSD hinaus.")
    return end


def linux_partitions(mbr: bytes) -> list[tuple[int, int]]:
    """(offset, length) in bytes of every Linux partition."""
    return [(start * SECTOR_BYTES, count * SECTOR_BYTES) for part_type, start, count in _entries(mbr) if part_type == LINUX]
