# Klipper Image Backup Manager (KIBM)

KIBM is a safety-first dashboard and full raw SD-card imaging tool for Klipper hosts. It reads the complete system microSD and streams an uncompressed `.img` directly to the browser, where the user chooses the PC save location. It does **not** modify boot files, partitions, EEPROM, or printer configuration, and it does not yet provide recovery or shrinking.

## Current status

- Phase 1 architecture and safety boundaries: documented in [`docs/architecture.md`](docs/architecture.md).
- Dashboard, Pi-account authentication, local HTTPS, backup preflight, and full-card browser streaming: implemented; hardware validation remains open.
- Phase 3 recovery boot: a plan-only TRYBOOT bundle generator validates a supplied initramfs and can inspect a raw disk image read-only. It never writes to boot media. A Pi-specific initramfs and real hardware boot/return test are still required.
- Shrinking and recovery: unavailable. The current raw image is a live capture; files can change while the Pi is running.

Do not treat this release as a backup or recovery utility. The dashboard will say so explicitly.

## Development

Python 3.9+ is required for local development. On the Pi, the installer handles system packages, certificates, permissions, authentication, and services:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
uvicorn kibm.main:app --host 127.0.0.1 --port 8765
```

Install on a supported Pi with `sudo ./install.sh`, then open the HTTPS address printed by the installer. Confirm its local certificate once in the browser. Backup requires the existing Linux account password; KIBM does not change or store it. Do not configure public port forwarding.

## Safety

The web service runs unprivileged. A small root-owned broker validates the existing Pi account password through PAM; it cannot run arbitrary commands. Moonraker being unavailable or returning an unknown printer state blocks imaging. See [`docs/security.md`](docs/security.md).

## License

MIT. See [`LICENSE`](LICENSE).
