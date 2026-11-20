# Security model

- The web process is unprivileged. It can read only `/dev/mmcblk0`; systemd blocks access to other devices. It exposes no reboot, shell, or filesystem mutation API.
- The dashboard diagnosis is available on the LAN. Starting or downloading an image requires a session authenticated with the existing account that invoked `sudo ./install.sh`.
- A root-owned local Unix-socket broker accepts requests only from the `kibm` UID and checks the supplied password using the system PAM stack. It exposes no shell or arbitrary privileged operation.
- The installer grants the installing Pi account passwordless sudo access only to the root-owned `/usr/local/sbin/kibm-admin` wrapper. The wrapper allows fixed KIBM status, logs, USB diagnosis, restart, and repository update actions and rejects arbitrary commands.
- The dashboard's update action requires the authenticated Pi session, CSRF protection, and a separate user confirmation. The root media worker can start only the fixed `kibm-update.service`; that unit installs exactly the release the user confirmed in the dashboard and aborts if a newer release appeared since. The confirmed commit is handed over in a root-owned file the `kibm` account cannot write. A manual `sudo kibm-admin update` installs the newest signed release.
- Updates come only from release tags `vX.Y.Z` / `vX.Y.ZbN`. `kibm-fetch-release` requires an annotated tag with an SSH signature that `git verify-tag` accepts against `/etc/kibm/allowed_signers`, and the tag must match the version in `pyproject.toml`. A push to `main`, a lightweight tag, or a tag signed by another key installs nothing. The key file comes from the repository (`security/allowed_signers`) at the first installation; afterwards only a correctly signed release can replace it. Whoever holds the release key can still install code as root on every Pi, so keep it protected.
- The browser connection uses a locally generated HTTPS certificate. The user confirms it once in the browser. The password is sent only over HTTPS, is not changed, and is not persisted by KIBM.
- Image creation streams the raw system card to the browser without staging the image on the Pi. The browser save dialog selects the PC destination. A live capture can include files changing during the transfer. A reported active or paused print blocks imaging; when Moonraker is unavailable, the user must explicitly confirm that no print is running or will start, and live monitoring is unavailable.
- Existing USB images are inspected read-only by parsing fixed MBR and ext4 metadata fields. KIBM offers PiShrink only when the free-space estimate exceeds 256 MB; unrecognized filesystems and compact images are not modified.
- The preflight endpoint is a GET and does not change system state. The image route is authenticated, requires explicit confirmation, and rechecks the card identity and any available printer state during streaming.
- Responses disable caching and framing. JSON output does not include environment variables, tokens, or command output.
- Moonraker is only queried at loopback; no caller-provided URL is accepted.
- `lsblk` is invoked with fixed arguments and a timeout. Its output is parsed as data, never as shell input.
- Keep port 8765 on the trusted LAN and do not configure public port forwarding.

The installer provisions packages, TLS, PAM, service permissions, and the KIBM systemd units. Recovery boot and restore remain unavailable; USB image shrinking is implemented but still requires hardware validation.
