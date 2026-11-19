# Read-only API

The current read-only API does not require a login. Keep it on a trusted local network.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/system` | Host, OS, kernel, and architecture |
| `GET` | `/api/storage` | `lsblk` devices and root filesystem usage |
| `GET` | `/api/klipper/status` | Moonraker print state and Klippy readiness |
| `GET` | `/api/recovery/status` | Explicit unavailable status for recovery support |
| `GET` | `/api/backup/preflight` | Read-only checks; always reports backup and recovery preparation unavailable |

OpenAPI docs are disabled in this alpha. No endpoint starts a backup, restarts the printer, runs a shell command, changes partitions, or downloads an image.
