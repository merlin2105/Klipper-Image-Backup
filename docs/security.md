# Security model

- The web process is unprivileged. It can read only `/dev/mmcblk0`; systemd blocks access to other devices. It exposes no reboot, shell, or filesystem mutation API.
- The dashboard diagnosis is available on the LAN. Starting or downloading an image requires a session authenticated with the existing account that invoked `sudo ./install.sh`.
- A root-owned local Unix-socket broker accepts requests only from the `kibm` UID and checks the supplied password using the system PAM stack. It exposes no shell or arbitrary privileged operation.
- The browser connection uses a locally generated HTTPS certificate. The user confirms it once in the browser. The password is sent only over HTTPS, is not changed, and is not persisted by KIBM.
- Image creation streams the raw system card to the browser without staging the image on the Pi. The browser save dialog selects the PC destination. A live capture can include files changing during the transfer; a printer not in a known idle state blocks imaging.
- The preflight endpoint is a GET and does not change system state. The image route is authenticated, requires explicit confirmation, and rechecks the card identity and printer state during streaming.
- Responses disable caching and framing. JSON output does not include environment variables, tokens, or command output.
- Moonraker is only queried at loopback; no caller-provided URL is accepted.
- `lsblk` is invoked with fixed arguments and a timeout. Its output is parsed as data, never as shell input.
- Keep port 8765 on the trusted LAN and do not configure public port forwarding.

The current installer provisions packages, TLS, PAM, service permissions, and both systemd units. Recovery and shrinking are not implemented.
