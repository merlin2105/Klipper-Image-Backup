from __future__ import annotations

import json
import logging
import os
import pwd
import socket
import socketserver
import struct
from pathlib import Path
from typing import Optional

SOCKET_PATH = Path("/run/kibm-auth/auth.sock")
ADMIN_USER_PATH = Path("/etc/kibm/admin-user")
MAX_REQUEST_BYTES = 2048
logger = logging.getLogger("kibm.auth")


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
            if not isinstance(payload, dict):
                self._reply(False)
                return
            if payload.get("action") == "health":
                username = ADMIN_USER_PATH.read_text(encoding="utf-8").strip()
                pwd.getpwnam(username)
                import PAM

                PAM.pam().start("kibm")
                self.wfile.write(b'{"ready":true}\n')
                return
            password = payload.get("password")
            if not isinstance(password, str) or not 1 <= len(password) <= 512 or "\0" in password:
                self._reply(False)
                return
            username = ADMIN_USER_PATH.read_text(encoding="utf-8").strip()
            import PAM

            auth = PAM.pam()
            auth.start("kibm")
            auth.set_item(PAM.PAM_USER, username)
            auth.set_item(PAM.PAM_CONV, self._pam_conversation(username, password))
            try:
                auth.authenticate()
                auth.acct_mgmt()
                authenticated = True
            except PAM.error as exc:
                error_code = exc.args[0] if exc.args else None
                if error_code != PAM.PAM_AUTH_ERR:
                    logger.error("Pi account PAM check failed with code %s", error_code)
                    self._reply(False, error="authentication_unavailable")
                    return
                authenticated = False
        except Exception as exc:
            # Keep operational failures distinct from a rejected password. Never log
            # the request payload or password.
            logger.error("Pi account authentication failed in the PAM broker (%s)", type(exc).__name__)
            self._reply(False, error="authentication_unavailable")
            return
        self._reply(authenticated)

    @staticmethod
    def _pam_conversation(username: str, password: str):
        import PAM

        def respond(_auth, queries, _user_data):
            answers = []
            for _query, prompt_type in queries:
                if prompt_type == PAM.PAM_PROMPT_ECHO_ON:
                    answer = username
                elif prompt_type == PAM.PAM_PROMPT_ECHO_OFF:
                    answer = password
                elif prompt_type in (PAM.PAM_PROMPT_ERROR_MSG, PAM.PAM_PROMPT_TEXT_INFO):
                    answer = ""
                else:
                    return None
                answers.append((answer, 0))
            return answers

        return respond

    def _reply(self, authenticated: bool, error: Optional[str] = None) -> None:
        result = {"authenticated": authenticated}
        if error:
            result["error"] = error
        self.wfile.write(json.dumps(result).encode("ascii") + b"\n")


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
