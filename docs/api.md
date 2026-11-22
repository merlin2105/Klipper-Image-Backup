# API

System diagnosis is available without login on the trusted local network. Image creation and download require HTTPS, the existing Pi account password, explicit confirmation that no print is running or will start, and a CSRF-protected session. A printer-reported active or paused job blocks imaging; when Moonraker is unavailable, the confirmation is the user's safety check.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/system` | Host, OS, kernel, and architecture |
| `GET` | `/api/storage` | `lsblk` devices and root filesystem usage |
| `GET` | `/api/klipper/status` | Moonraker print state and Klippy readiness |
| `GET` | `/api/recovery/status` | Explicit unavailable status for recovery support |
| `GET` | `/api/backup/preflight` | Read-only checks for active print state, supported card layout, and read access |
| `GET` | `/api/backup/usb/images` | Lists USB images with read-only shrinkability estimates |
| `GET` | `/api/session` | Reports whether the browser session is authenticated |
| `GET` | `/api/update` | Compares the installed version with the newest GitHub release tag and reports updater state (`?refresh=true` is honoured at most every two minutes) |
| `POST` | `/api/login` | Validates the existing Pi account password through the local auth broker |
| `POST` | `/api/logout` | Ends the authenticated session (CSRF-protected) |
| `POST` | `/api/update` | Starts a confirmed update to that release; the signature is verified before installing (authenticated and CSRF-protected) |
| `GET` | `/api/backup/usb/current` | Returns the media worker's current USB job with a download token for this session, so a reloaded page can resume it (authenticated) |
| `GET` | `/api/backup/nas` | Without a session only `{configured}`; with a session the saved NAS target (server, share, user, folder) — never the password |
| `POST` | `/api/backup/nas/discover` | Finds SMB servers (mDNS `_smb._tcp` and a port-445 probe of the Pi's /24) (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/shares` | Lists the disk shares of a server for the given credentials (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/check` | Mounts the saved share briefly and returns `{ok, display, free_bytes}` or `{ok: false, error}` (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/browse` | Lists sub-folders of a path on the share (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/mkdir` | Creates one sub-folder on the share (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/save` | Tests and saves the NAS target; an empty password keeps the saved one only for the same server, share and user (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/delete` | Removes the saved NAS target and its password file (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/images` | Lists `.img` files in the NAS backup folder; their IDs work with the USB shrink and delete routes (authenticated and CSRF-protected) |
| `POST` | `/api/backup/nas/start` | Starts a confirmed backup to the saved NAS folder; progress via the USB job routes (authenticated and CSRF-protected) |
| `POST` | `/api/backup/start` | Starts a confirmed full-card image job (authenticated and CSRF-protected) |
| `GET` | `/api/backup/jobs/{job_id}` | Returns authenticated transfer progress and SHA-256 |
| `GET` | `/api/backup/download/{job_id}?token=…` | Streams the complete raw card image to the browser (authenticated, one use) |

OpenAPI docs are disabled in this beta. No endpoint restarts the printer, accepts a shell command, or changes partitions.
