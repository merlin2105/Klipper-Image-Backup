# Security model

- The web process is unprivileged. There is no imaging, reboot, shell, or filesystem mutation API.
- Set a unique `KIBM_ADMIN_PASSWORD` and random `KIBM_SESSION_SECRET`; startup fails if either is absent or too short.
- Sessions are random in-memory identifiers, HTTP-only, SameSite=Strict, and short-lived. Restarting the process invalidates all sessions.
- Session identifiers are signed with the configured secret, and login attempts are limited to five failures per client address in ten minutes.
- Mutating API requests require a session-bound CSRF token and same-origin `Origin` header. The only current POST endpoint is read-only preflight.
- Responses disable caching and framing. JSON output does not include environment variables, tokens, or command output.
- Moonraker is only queried at loopback; no caller-provided URL is accepted.
- `lsblk` is invoked with fixed arguments and a timeout. Its output is parsed as data, never as shell input.
- The first installer binds the diagnostic service on all local interfaces over HTTP so a browser on the LAN can reach it. HTTP does not protect passwords or cookies in transit. Restrict access to a trusted LAN and do not configure public port forwarding; use a trusted HTTPS reverse proxy before any wider exposure. The current interface has no image, reboot, or privileged operation endpoints.

Future privileged operations require a separate design review, a fixed-operation broker, explicit authorization, audit records, and hardware-specific failure recovery.
