import io
import json
import struct
import sys
import types

import pytest

from kibm import auth_broker

KIBM_UID = 4242


class _Connection:
    def __init__(self, uid):
        self.uid = uid

    def getsockopt(self, *_args):
        return struct.pack("3i", 1, self.uid, self.uid)


class _PamError(Exception):
    pass


def _fake_pam(monkeypatch, *, password="right", error_code=None):
    module = types.ModuleType("PAM")
    module.error = _PamError
    module.PAM_USER, module.PAM_CONV, module.PAM_AUTH_ERR = 1, 2, 7
    module.PAM_PROMPT_ECHO_OFF, module.PAM_PROMPT_ECHO_ON = 1, 2
    module.PAM_PROMPT_ERROR_MSG, module.PAM_PROMPT_TEXT_INFO = 3, 4

    class Pam:
        def __init__(self):
            self.items = {}

        def start(self, _service):
            pass

        def set_item(self, key, value):
            self.items[key] = value

        def authenticate(self):
            answers = self.items[module.PAM_CONV](self, [("Password:", module.PAM_PROMPT_ECHO_OFF)], None)
            if error_code is not None:
                raise _PamError(error_code)
            if answers[0][0] != password:
                raise _PamError(module.PAM_AUTH_ERR)

        def acct_mgmt(self):
            pass

    module.pam = Pam
    monkeypatch.setitem(sys.modules, "PAM", module)


def _ask(monkeypatch, tmp_path, raw: bytes, *, uid=KIBM_UID) -> dict:
    admin_user = tmp_path / "admin-user"
    admin_user.write_text("pi\n")
    monkeypatch.setattr(auth_broker, "ADMIN_USER_PATH", admin_user)
    monkeypatch.setattr(auth_broker.pwd, "getpwnam", lambda _name: types.SimpleNamespace(pw_uid=KIBM_UID))
    handler = auth_broker.AuthHandler.__new__(auth_broker.AuthHandler)
    handler.request = _Connection(uid)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.handle()
    return json.loads(handler.wfile.getvalue())


def test_correct_password_is_accepted(monkeypatch, tmp_path) -> None:
    _fake_pam(monkeypatch)
    assert _ask(monkeypatch, tmp_path, b'{"password":"right"}\n') == {"authenticated": True}


def test_wrong_password_is_rejected_without_error(monkeypatch, tmp_path) -> None:
    _fake_pam(monkeypatch)
    assert _ask(monkeypatch, tmp_path, b'{"password":"wrong"}\n') == {"authenticated": False}


def test_pam_failure_is_reported_as_unavailable(monkeypatch, tmp_path) -> None:
    _fake_pam(monkeypatch, error_code=99)
    assert _ask(monkeypatch, tmp_path, b'{"password":"right"}\n') == {
        "authenticated": False, "error": "authentication_unavailable",
    }


@pytest.mark.parametrize("raw", [
    b'{"password":"right"}',                       # no newline
    b'{"password":"' + b"x" * 3000 + b'"}\n',    # oversized
    b"[]\n",                                       # not an object
    b'{"password":""}\n',
    b'{"password":"a\\u0000b"}\n',
    b'{"password":42}\n',
])
def test_malformed_requests_are_rejected(monkeypatch, tmp_path, raw) -> None:
    _fake_pam(monkeypatch)
    assert _ask(monkeypatch, tmp_path, raw) == {"authenticated": False}


def test_other_local_users_are_rejected(monkeypatch, tmp_path) -> None:
    _fake_pam(monkeypatch)
    assert _ask(monkeypatch, tmp_path, b'{"password":"right"}\n', uid=1000) == {"authenticated": False}
