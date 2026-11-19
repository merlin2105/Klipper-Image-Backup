# Security model

- The web process is unprivileged. There is no imaging, reboot, shell, or filesystem mutation API.
- The read-only dashboard has no login and its API is available to clients on the local network. Keep port 8765 on a trusted LAN and do not configure public port forwarding.
- The current preflight endpoint is a GET and does not change system state.
- Responses disable caching and framing. JSON output does not include environment variables, tokens, or command output.
- Moonraker is only queried at loopback; no caller-provided URL is accepted.
- `lsblk` is invoked with fixed arguments and a timeout. Its output is parsed as data, never as shell input.
- The first installer binds the diagnostic service on all local interfaces over HTTP so a browser on the LAN can reach it. The current interface has no image, reboot, or privileged operation endpoints.

Future privileged operations require a separate design review, a fixed-operation broker, explicit authorization, audit records, and hardware-specific failure recovery.
