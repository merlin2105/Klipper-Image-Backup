import pytest

from kibm import updates


def test_version_from_project_reads_project_version() -> None:
    text = '[build-system]\nversion = "9.9.9"\n\n[project]\nname = "x"\nversion = "0.2.10b1"\n'
    assert updates._version_from_project(text) == "0.2.10b1"


@pytest.mark.parametrize("text", ['[project]\nname = "x"\n', '[project]\nversion = "latest"\n'])
def test_version_from_project_rejects_missing_or_invalid_version(text) -> None:
    with pytest.raises(ValueError):
        updates._version_from_project(text)


@pytest.mark.parametrize(("version", "label"), [
    ("0.2.10b1", "v0.2.10 Beta"), ("1.0.0", "v1.0.0"), (None, None), ("", None),
])
def test_version_label(version, label) -> None:
    assert updates._version_label(version) == label


def test_forced_refresh_is_throttled(monkeypatch) -> None:
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(updates, "_cached_at", 0.0)
    monkeypatch.setattr(updates, "_cached_commit", None)
    monkeypatch.setattr(updates, "_cached_error", None)
    monkeypatch.setattr(updates, "_read_latest_release", lambda: calls.append(1) or {"sha": "a" * 40, "version": "0.3.0"})

    updates._latest_commit(force=True)
    clock[0] += 10
    updates._latest_commit(force=True)
    assert len(calls) == 1
    clock[0] += updates.MIN_FORCE_REFRESH_SECONDS
    updates._latest_commit(force=True)
    assert len(calls) == 2


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _limit):
        import json
        return json.dumps(self.body).encode()


def test_latest_release_picks_highest_version_tag(monkeypatch) -> None:
    tags = [
        {"name": "v0.3.0b2", "commit": {"sha": "b" * 40}},
        {"name": "v0.3.0", "commit": {"sha": "c" * 40}},
        {"name": "v0.2.12", "commit": {"sha": "d" * 40}},
        {"name": "nightly", "commit": {"sha": "e" * 40}},
        {"name": "v9.9", "commit": {"sha": "f" * 40}},
    ]
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(tags))
    latest = updates._read_latest_release()
    assert (latest["tag"], latest["sha"], latest["version"]) == ("v0.3.0", "c" * 40, "0.3.0")


def test_latest_release_without_tags_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response([]))
    with pytest.raises(ValueError, match="kein Release-Tag"):
        updates._read_latest_release()


@pytest.mark.parametrize(("latest_version", "current_version", "current", "dirty", "expected"), [
    ("0.3.0", "0.2.10b1", "x" * 40, False, True),     # newer release
    ("0.3.0", "0.3.0b2", "x" * 40, False, True),      # final after beta
    ("0.2.10b1", "0.3.0", "x" * 40, False, False),    # never downgrade
    ("0.3.0", "0.3.0", "a" * 40, False, False),       # already installed
    ("0.3.0", "0.3.0", "x" * 40, False, True),        # same version, untagged build
    ("0.3.0", "0.3.0", "a" * 40, True, True),         # local changes
    ("0.3.0", None, None, False, True),               # unknown installation
])
def test_update_offer_compares_versions(latest_version, current_version, current, dirty, expected) -> None:
    latest = {"sha": "a" * 40, "version": latest_version}
    assert updates._is_newer(latest, current, dirty, current_version) is expected
