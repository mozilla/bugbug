"""Tests for HackbotContext capabilities and results plumbing."""

import json
from pathlib import Path

import pytest
from hackbot_runtime import HackbotContext
from hackbot_runtime.changes import ChangeSet
from hackbot_runtime.config import FirefoxConfig, HackbotConfig, SourceConfig


def _hb(tmp_path, config: HackbotConfig) -> HackbotContext:
    hb = HackbotContext(run_id="local-test", artifacts_dir=tmp_path / "artifacts")
    hb._config = config
    return hb


def test_source_repo_without_declaration_raises(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    with pytest.raises(RuntimeError, match="\\[source\\]"):
        hb.source_repo


def test_firefox_without_declaration_raises(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    with pytest.raises(RuntimeError, match="\\[firefox\\]"):
        hb.firefox


def test_firefox_disabled_raises(tmp_path):
    cfg = HackbotConfig(
        source=SourceConfig(repo_url="x"), firefox=FirefoxConfig(enabled=False)
    )
    hb = _hb(tmp_path, cfg)
    with pytest.raises(RuntimeError, match="\\[firefox\\]"):
        hb.firefox


def test_source_repo_prepares_and_honors_env_override(tmp_path, monkeypatch):
    calls = []

    def fake_ensure(
        path: Path, repo_url: str, ref: str | None = None, depth: int | None = None
    ) -> None:
        calls.append((path, repo_url, ref, depth))

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.setenv("SOURCE_REPO", str(tmp_path / "from-env"))
    monkeypatch.delenv("SOURCE_REF", raising=False)

    cfg = HackbotConfig(
        source=SourceConfig(
            repo_url="https://example.com/r.git",
            checkout_path=Path("/from/toml"),
        )
    )
    hb = _hb(tmp_path, cfg)

    assert hb.source_repo == tmp_path / "from-env"
    assert calls == [(tmp_path / "from-env", "https://example.com/r.git", None, None)]


def test_source_repo_honors_source_ref_env(tmp_path, monkeypatch):
    calls = []

    def fake_ensure(
        path: Path, repo_url: str, ref: str | None = None, depth: int | None = None
    ) -> None:
        calls.append((path, repo_url, ref, depth))

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.setenv("SOURCE_REF", "deadbeef")

    cfg = HackbotConfig(
        source=SourceConfig(repo_url="r", checkout_path=Path("/from/toml"))
    )
    hb = _hb(tmp_path, cfg)

    assert hb.source_repo == Path("/from/toml")
    assert calls == [(Path("/from/toml"), "r", "deadbeef", None)]


def test_source_repo_uses_toml_path_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo", lambda *a, **k: None
    )
    cfg = HackbotConfig(
        source=SourceConfig(repo_url="r", checkout_path=Path("/from/toml"))
    )
    hb = _hb(tmp_path, cfg)
    assert hb.source_repo == Path("/from/toml")


def test_results_plumbing(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())

    assert hb.run_id == "local-test"

    log = tmp_path / "agent.log"
    log.write_text("hello")
    key = hb.publish_file("logs/agent.log", log)
    assert key == "logs/agent.log"
    written = tmp_path / "artifacts" / "local-test" / "logs" / "agent.log"
    assert written.read_text() == "hello"

    hb.actions.record("bugzilla.update_bug", {"bug_id": 1}, reasoning="r")
    assert hb.actions.actions[0]["type"] == "bugzilla.update_bug"


def _hb_with_source(tmp_path, monkeypatch):
    """Wire a context to publish changes without a real checkout.

    Sets a recorded source base and mocks changes.collect so
    publish_changes() runs its body.
    """
    cfg = HackbotConfig(source=SourceConfig(repo_url="https://example.com/r.git"))
    hb = _hb(tmp_path, cfg)
    hb._source_base = "basecommit"
    # source_repo would normally clone; publish_changes only passes it through
    # to the (mocked) changes helpers, so a bare path is enough here.
    monkeypatch.setattr(
        type(hb), "source_repo", property(lambda self: tmp_path / "src")
    )
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.collect",
        lambda repo, base, repo_url: ChangeSet(patch=b"x", metadata={"base": base}),
    )
    return hb


def test_publish_changes_builds_phabricator_diff_when_action_recorded(
    tmp_path, monkeypatch
):
    hb = _hb_with_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff",
        lambda repo, base, repo_url: {
            "diff": {"changes": [], "sourceControlBaseRevision": base},
            "local_commits": {"node": {"author": "A"}},
        },
    )
    hb.actions.record("phabricator.submit_patch", {"bug_id": 1}, reasoning="r")

    hb.publish_changes()

    # One artifact holds both the creatediff payload and the local:commits data.
    submission = json.loads(
        (
            tmp_path / "artifacts" / "local-test" / "changes" / "phabricator_diff.json"
        ).read_text()
    )
    assert submission["diff"]["sourceControlBaseRevision"] == "basecommit"
    assert submission["local_commits"]["node"]["author"] == "A"


def test_publish_changes_skips_phabricator_diff_without_action(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff",
        lambda *a, **k: called.append(a) or {},
    )
    hb.actions.record("bugzilla.add_comment", {"bug_id": 1}, reasoning="r")

    hb.publish_changes()

    assert called == []
    written = (
        tmp_path / "artifacts" / "local-test" / "changes" / "phabricator_diff.json"
    )
    assert not written.exists()
