import struct

import pytest

from kibm import partitions


def _mbr(entries, signature=b"\x55\xaa"):
    mbr = bytearray(512)
    for index, (part_type, start, count) in enumerate(entries):
        base = 446 + index * 16
        mbr[base + 4] = part_type
        struct.pack_into("<II", mbr, base + 8, start, count)
    mbr[510:512] = signature
    return bytes(mbr)


def test_capture_ends_at_the_last_partition() -> None:
    # Layout of the real Klipper card: FAT boot + ext4 root ending at 31,914,983,424 bytes.
    mbr = _mbr([(0x0C, 8192, 524288), (0x83, 532480, 61801472)])
    assert partitions.capture_bytes(mbr, 32_006_733_824) == 31_914_983_424


def test_capture_uses_the_furthest_partition_even_if_listed_first() -> None:
    mbr = _mbr([(0x83, 532480, 1000), (0x0C, 8192, 524288)])
    assert partitions.capture_bytes(mbr, 10 ** 9) == (532480 + 1000) * 512


def test_extended_partition_container_covers_logical_partitions() -> None:
    mbr = _mbr([(0x0C, 8192, 524288), (0x05, 532480, 2000)])
    assert partitions.capture_bytes(mbr, 10 ** 9) == (532480 + 2000) * 512


@pytest.mark.parametrize("mbr", [
    _mbr([(0x0C, 8192, 524288)], signature=b"\x00\x00"),   # no partition table
    _mbr([]),                                              # empty table
    _mbr([(0xEE, 1, 0xFFFFFFFF)]),                         # GPT: not supported
    b"\x00" * 100,                                         # short read
])
def test_unknown_layouts_are_rejected(mbr) -> None:
    with pytest.raises(ValueError):
        partitions.capture_bytes(mbr, 10 ** 9)


def test_partition_beyond_the_device_is_rejected() -> None:
    mbr = _mbr([(0x83, 2048, 10 ** 6)])
    with pytest.raises(ValueError):
        partitions.capture_bytes(mbr, 2048 * 512 + 10)


def test_linux_partitions_are_listed_with_byte_offsets() -> None:
    mbr = _mbr([(0x0C, 8192, 524288), (0x83, 532480, 61801472)])
    assert partitions.linux_partitions(mbr) == [(532480 * 512, 61801472 * 512)]
