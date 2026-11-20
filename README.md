# Klipper Image Backup Manager (KIBM)

KIBM is a safety-first SD-card image tool for Klipper hosts. PC mode streams an uncompressed full-card `.img` directly to the browser. USB mode prepares an explicitly selected USB drive as exFAT, writes the full image there, and can optionally create a genuinely shrunk image with PiShrink on the Pi. The system microSD, boot files, EEPROM, and printer configuration are not modified.

## Current status

- Phase 1 architecture and safety boundaries: documented in [`docs/architecture.md`](docs/architecture.md).
- Dashboard, Pi-account authentication, local HTTPS, backup preflight, and full-card browser streaming: implemented.
- USB destination selection, exFAT formatting, full-image capture, optional PiShrink, and browser download: implemented in the repository; USB hardware validation remains open.
- Existing USB `.img` files are checked from their partition table and ext4 free-space metadata, regardless of filename. KIBM offers a new shrink only when it estimates at least 256 MB of possible savings; unclear or already compact images are left alone.
- Phase 3 recovery boot: a plan-only TRYBOOT bundle generator validates a supplied initramfs and can inspect a raw disk image read-only. It never writes to boot media. A Pi-specific initramfs and real hardware boot/return test are still required.
- Recovery boot and restore: unavailable. The live capture can contain files that change while the Pi is running.

USB formatting deletes all existing files on the selected drive. Shrinking operates on a staged image copy, not the running system card. A completed checksum validates file data, not whether a restore will boot.

## Development

Python 3.9+ is required for local development. On the Pi, the installer handles system packages, certificates, permissions, authentication, and services:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
uvicorn kibm.main:app --host 127.0.0.1 --port 8765
```

Install on a supported Pi with `sudo ./install.sh`, then open the HTTPS address printed by the installer. Confirm its local certificate once in the browser. Backup requires the existing Linux account password; KIBM does not change or store it. Do not configure public port forwarding.

The dashboard checks GitHub's `main` branch for a newer commit. An authenticated user can confirm an update there; KIBM downloads that exact commit, runs the normal installer in a separate systemd unit, and restarts its services. The dashboard is briefly unavailable during installation. The check requires outbound HTTPS access to GitHub.

## Safety

The web service runs unprivileged. A root-owned PAM broker validates the existing Pi account password. A separate local media worker accepts only fixed image actions and revalidates USB identity before formatting; it does not expose arbitrary command execution. A reported active or paused print blocks capture. If Moonraker is unavailable, the user must explicitly confirm that no print is running or will start; live print monitoring is then unavailable. See [`docs/security.md`](docs/security.md).

For remote KIBM maintenance, the installer grants the installing Pi account passwordless access only to the fixed `kibm-admin` wrapper (`status`, `logs`, USB diagnosis, `restart`, and `update`). The wrapper accepts no arbitrary commands.

## License

MIT. See [`LICENSE`](LICENSE).
