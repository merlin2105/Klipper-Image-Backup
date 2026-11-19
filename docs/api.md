# API

System diagnosis is available without login on the trusted local network. Image creation and download require HTTPS, the existing Pi account password, a confirmed idle printer, and a CSRF-protected session.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/system` | Host, OS, kernel, and architecture |
| `GET` | `/api/storage` | `lsblk` devices and root filesystem usage |
| `GET` | `/api/klipper/status` | Moonraker print state and Klippy readiness |
| `GET` | `/api/recovery/status` | Explicit unavailable status for recovery support |
| `GET` | `/api/backup/preflight` | Read-only checks for printer standby, supported card layout, and read access |
| `GET` | `/api/session` | Reports whether the browser session is authenticated |
| `POST` | `/api/login` | Validates the existing Pi account password through the local auth broker |
| `POST` | `/api/logout` | Ends the authenticated session (CSRF-protected) |
| `POST` | `/api/backup/start` | Starts a confirmed full-card image job (authenticated and CSRF-protected) |
| `GET` | `/api/backup/jobs/{job_id}` | Returns authenticated transfer progress and SHA-256 |
| `GET` | `/api/backup/download/{job_id}?token=…` | Streams the complete raw card image to the browser (authenticated, one use) |

OpenAPI docs are disabled in this alpha. No endpoint restarts the printer, runs a shell command, or changes partitions.
