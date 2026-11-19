# Klipper Image Backup Manager (KIBM)

KIBM is an early, safety-first project for inspecting a Klipper host and preparing a recoverable, offline SD-card backup workflow. The current milestone provides a read-only dashboard and API. It does **not** modify boot files, reboot the printer, shrink filesystems, or create disk images.

## Current status

- Phase 1 architecture and safety boundaries: documented in [`docs/architecture.md`](docs/architecture.md).
- Phase 2 read-only service and dashboard: implemented; deployment and Raspberry Pi validation remain open.
- Phase 3 recovery boot: a plan-only TRYBOOT bundle generator validates a supplied initramfs and never writes to boot media. A Pi-specific initramfs and real hardware boot/return test are still required.
- Imaging, shrinking, streaming, and restore validation: unavailable.

Do not treat this release as a backup or recovery utility. The dashboard will say so explicitly.

## Development

Python 3.11+ is required. Install dependencies and run the app with an explicit password and session secret:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
export KIBM_ADMIN_PASSWORD='use-a-long-unique-password'
export KIBM_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
uvicorn kibm.main:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. The service refuses to start without both secrets. On a network-facing deployment use HTTPS through a trusted reverse proxy; LAN HTTP is not a secure browser context and does not protect credentials in transit.

## Safety

KIBM currently runs without root privileges and exposes no endpoint that runs commands or changes the host. Moonraker being unavailable or returning an unknown printer state is always treated as **not safe to reboot**. See [`docs/security.md`](docs/security.md).

## License

MIT. See [`LICENSE`](LICENSE).
