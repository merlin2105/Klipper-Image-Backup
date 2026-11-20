import subprocess
from types import SimpleNamespace

import pytest

from kibm import media_worker


def _handler(monkeypatch, tmp_path, run):
    replies = []
    monkeypatch.setattr(media_worker, "_job", None)
    monkeypatch.setattr(media_worker, "VOLUME_PATH", tmp_path / "volume")
    monkeypatch.setattr(media_worker, "UPDATE_COMMIT_PATH", tmp_path / "update-commit")
    monkeypatch.setattr(media_worker, "_run", run)
    handler = media_worker.MediaHandler.__new__(media_worker.MediaHandler)
    handler._reply = replies.append
    return handler, replies


def test_update_start_records_confirmed_commit_before_starting_unit(monkeypatch, tmp_path) -> None:
    commands = []

    def run(args, timeout=30):
        commands.append(args)
        if args[:2] == ["systemctl", "start"]:
            assert (tmp_path / "update-commit").read_text() == "b" * 40 + "\n"
        return SimpleNamespace(stdout="inactive\n")

    handler, replies = _handler(monkeypatch, tmp_path, run)
    handler._update_start({"action": "update_start", "commit": "b" * 40})

    assert ["systemctl", "start", "--no-block", "kibm-update.service"] in commands
    assert (tmp_path / "update-commit").stat().st_mode & 0o777 == 0o600
    assert replies == [{"state": "running"}]


@pytest.mark.parametrize("commit", [None, "", "B" * 40, "b" * 39, "b" * 40 + "\n", "../../etc/passwd"])
def test_update_start_rejects_missing_or_invalid_commit(monkeypatch, tmp_path, commit) -> None:
    commands = []
    handler, _ = _handler(monkeypatch, tmp_path, lambda args, timeout=30: commands.append(args) or SimpleNamespace(stdout="inactive\n"))

    with pytest.raises(ValueError):
        handler._update_start({"action": "update_start", "commit": commit})
    assert not any(args[:2] == ["systemctl", "start"] for args in commands)
    assert not (tmp_path / "update-commit").exists()


def test_update_start_removes_commit_when_unit_cannot_start(monkeypatch, tmp_path) -> None:
    def run(args, timeout=30):
        if args[:2] == ["systemctl", "start"]:
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(stdout="inactive\n")

    handler, _ = _handler(monkeypatch, tmp_path, run)
    with pytest.raises(subprocess.CalledProcessError):
        handler._update_start({"action": "update_start", "commit": "c" * 40})
    assert not (tmp_path / "update-commit").exists()
