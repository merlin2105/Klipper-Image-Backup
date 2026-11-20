"""Runs scripts/kibm-fetch-release against a local repository with real SSH signatures."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "kibm-fetch-release"
pytestmark = pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("ssh-keygen")), reason="git and ssh-keygen are required"
)
GIT_ENV = {
    **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _key(tmp_path: Path, name: str) -> Path:
    key = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True,
                              text=True, env=GIT_ENV).stdout.strip()

    def release(self, version: str, tag: str, *, key=None, lightweight=False) -> str:
        (self.path / "pyproject.toml").write_text(f'[project]\nname = "kibm"\nversion = "{version}"\n')
        self.git("add", "pyproject.toml")
        self.git("commit", "-q", "-m", f"Release {version}")
        if lightweight:
            self.git("tag", tag)
        elif key is None:
            self.git("tag", "-a", tag, "-m", tag)
        else:
            self.git("-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", tag, "-m", tag)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def setup(tmp_path):
    release_key = _key(tmp_path, "release")
    signers = tmp_path / "allowed_signers"
    signers.write_text(f'kibm-release namespaces="git" {release_key.with_suffix(".pub").read_text()}')
    return Repo(tmp_path / "repo"), release_key, signers, tmp_path


def _fetch(repo: Repo, signers: Path, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), f"file://{repo.path}", str(signers), str(output)],
                          capture_output=True, text=True)


def test_installs_newest_signed_release_with_beta_before_final(setup) -> None:
    repo, key, signers, tmp_path = setup
    repo.release("0.2.9", "v0.2.9", key=key)
    repo.release("0.3.0b2", "v0.3.0b2", key=key)
    final = repo.release("0.3.0", "v0.3.0", key=key)
    repo.release("0.3.0b1", "v0.3.0b1", key=key)

    result = _fetch(repo, signers, tmp_path / "out")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["v0.3.0", final]
    assert 'version = "0.3.0"' in (tmp_path / "out" / "pyproject.toml").read_text()
    assert not (tmp_path / "out" / ".git").exists()


@pytest.mark.parametrize("variant", ["unsigned", "foreign_key", "lightweight"])
def test_rejects_tags_without_a_trusted_signature(setup, variant) -> None:
    repo, key, signers, tmp_path = setup
    repo.release("0.2.9", "v0.2.9", key=key)
    if variant == "unsigned":
        repo.release("0.3.0", "v0.3.0")
    elif variant == "foreign_key":
        repo.release("0.3.0", "v0.3.0", key=_key(tmp_path, "attacker"))
    else:
        repo.release("0.3.0", "v0.3.0", lightweight=True)

    result = _fetch(repo, signers, tmp_path / "out")

    assert result.returncode != 0
    assert "Signatur" in result.stderr
    assert not (tmp_path / "out").exists()


def test_rejects_tag_that_does_not_match_project_version(setup) -> None:
    repo, key, signers, tmp_path = setup
    repo.release("0.3.0", "v0.4.0", key=key)
    result = _fetch(repo, signers, tmp_path / "out")
    assert result.returncode != 0
    assert "passt nicht" in result.stderr


def test_reports_missing_release_tags(setup) -> None:
    repo, _key_path, signers, tmp_path = setup
    repo.release("0.3.0", "not-a-release", lightweight=True)
    result = _fetch(repo, signers, tmp_path / "out")
    assert result.returncode != 0
    assert "kein Release-Tag" in result.stderr


def test_requires_a_trusted_key(setup) -> None:
    repo, key, _signers, tmp_path = setup
    repo.release("0.3.0", "v0.3.0", key=key)
    empty = tmp_path / "empty_signers"
    empty.write_text("")
    result = _fetch(repo, empty, tmp_path / "out")
    assert result.returncode != 0
    assert "Release-Schlüssel" in result.stderr
