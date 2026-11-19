# Read-only API

All `/api` endpoints except `GET /api/session` require a valid login session. Every `POST` additionally requires the session's `X-KIBM-CSRF` token and a same-origin `Origin` header.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/session` | Report login state and return the session CSRF token |
| `POST` | `/api/login` | Create an authenticated session |
| `POST` | `/api/logout` | End the session |
| `GET` | `/api/system` | Host, OS, kernel, and architecture |
| `GET` | `/api/storage` | `lsblk` devices and root filesystem usage |
| `GET` | `/api/klipper/status` | Moonraker print state and Klippy readiness |
| `GET` | `/api/recovery/status` | Explicit unavailable status for recovery support |
| `POST` | `/api/backup/preflight` | Read-only checks; always reports backup and recovery preparation unavailable |

OpenAPI docs are disabled in this alpha. No endpoint starts a backup, restarts the printer, runs a shell command, changes partitions, or downloads an image.
