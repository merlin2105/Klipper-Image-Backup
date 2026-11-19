from __future__ import annotations

import json
import os
import pwd
import socket
import socketserver
import struct
from pathlib import Path

SOCKET_PATH = Path("/run/kibm-auth/auth.sock")
ADMIN_USER_PATH = Path("/etc/kibm/admin-user")
MAX_REQUEST_BYTES = 2048


class AuthHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        expected_uid = pwd.getpwnam("kibm").pw_uid
        _, peer_uid, _ = struct.unpack(
            "3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
        if peer_uid != expected_uid:
            self._reply(False)
            return

        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            self._reply(False)
            return
        try:
            payload = json.loads(raw)
            password = payload.get("password")
            if not isinstance(password, str) or not 1 <= len(password) <= 512 or "\0" in password:
                self._reply(False)
                return
            username = ADMIN_USER_PATH.read_text(encoding="utf-8").strip()
            import pam

            authenticated = bool(pam.pam().authenticate(username, password, service="kibm"))
        except Exception:
            authenticated = False
        self._reply(authenticated)

    def _reply(self, authenticated: bool) -> None:
        self.wfile.write(json.dumps({"authenticated": authenticated}).encode("ascii") + b"\n")


class AuthServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    SOCKET_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    with AuthServer(str(SOCKET_PATH), AuthHandler) as server:
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
