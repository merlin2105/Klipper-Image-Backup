# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated: Python 3 and FastAPI for the local service, with HTML, CSS, and vanilla JavaScript for the dashboard, following the supplied architecture.

## Users

People running Klipper on a Raspberry Pi, including users without Linux administration experience. They need to see whether the printer and storage layout can be backed up safely.

## Product Purpose

KIBM will create and verify bootable, genuinely shrunk images of Klipper host SD cards, stream them to a PC, and help return the printer to normal operation. Success means an image is received and integrity checked, and a separately tested restore can boot on compatible hardware.

## Positioning

The distinguishing mechanism is offline imaging through a recovery environment, followed by actual filesystem and partition shrinking. Compression alone does not qualify as shrinking and must not be presented as making an image fit on a smaller card.

## Operating Context

The primary target is Raspberry Pi 4 running Raspberry Pi OS or Debian-based Klipper, Moonraker, and Mainsail or Fluidd, booting from microSD. Users start from the printer's web interface or a separate local KIBM URL and receive the image on their PC.

## Capabilities and Constraints

- The first release targets Pi 4, a FAT boot partition, and ext4 root; broader hardware support is future work.
- Hardware, OS, boot mode, partitions, Klipper, Moonraker, and Mainsail/Fluidd should be detected. Unknown layouts must be rejected safely.
- Active and paused print jobs, unavailable Moonraker, and unknown printer state block reboot preparation.
- Installer must be idempotent; boot-file backups and explicit confirmation are required before any future boot changes. Partition changes require separate explicit confirmation.
- The service must be authenticated, CSRF-protected for mutations, and unprivileged. It must not expose arbitrary commands or public administrative endpoints.
- Only measured values may appear as progress, speed, size estimates, or remaining time. A checksum does not prove that a restore boots.
- The current code implements read-only diagnosis only. Recovery boot, offline imaging, shrinking, streaming, and restoration are unavailable and unverified.
- Open question: exact recovery initramfs construction and supported boot-image matrix require Raspberry Pi 4 hardware testing before enablement.

## Brand Commitments

The product name is Klipper Image Backup Manager (KIBM). The interface should support a dark mode and feel familiar to Mainsail/Fluidd users without copying their design or modifying their source files.

## Evidence on Hand

The supplied project brief is the source for requirements. The current dashboard reads live host metadata, `lsblk`, filesystem usage, and Moonraker status where available. No Raspberry Pi hardware test, completed backup, successful restore, customer quote, or benchmark is available; none may be claimed.

## Product Principles

- Protect the source installation.
- Treat unknown hardware and printer state as unsafe.
- Distinguish implementation, verification, and real restore evidence.
- Explain blockers in language a non-specialist can act on.
- Do not fabricate measurements or readiness.

## Accessibility & Inclusion

The responsive interface must be usable on desktop, tablet, and phone, with keyboard access, readable contrast, visible focus, and plain-language status and error messages.
