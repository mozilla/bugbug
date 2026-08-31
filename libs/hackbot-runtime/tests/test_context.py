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


async def test_source_repo_without_declaration_raises(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    with pytest.raises(RuntimeError, match="\\[source\\]"):
        await hb.prepare_repo()


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


async def test_source_repo_prepares_and_honors_env_override(tmp_path, monkeypatch):
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

    assert await hb.prepare_repo() == tmp_path / "from-env"
    assert calls == [(tmp_path / "from-env", "https://example.com/r.git", None, None)]


async def test_source_repo_honors_source_ref_env(tmp_path, monkeypatch):
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

    assert await hb.prepare_repo() == Path("/from/toml")
    assert calls == [(Path("/from/toml"), "r", "deadbeef", None)]


async def test_source_repo_uses_toml_path_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo", lambda *a, **k: None
    )
    cfg = HackbotConfig(
        source=SourceConfig(repo_url="r", checkout_path=Path("/from/toml"))
    )
    hb = _hb(tmp_path, cfg)
    assert await hb.prepare_repo() == Path("/from/toml")


async def test_prepare_repo_explicit_ref_overrides_env(tmp_path, monkeypatch):
    refs = []
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo",
        lambda path, repo_url, ref=None, depth=None: refs.append(ref),
    )
    monkeypatch.setenv("SOURCE_REF", "from-env")
    cfg = HackbotConfig(source=SourceConfig(repo_url="r", checkout_path=Path("/x")))
    hb = _hb(tmp_path, cfg)

    # An explicit ref (e.g. a revision's base commit) wins over SOURCE_REF, and
    # is threaded straight to ensure_source_repo — no env mutation.
    await hb.prepare_repo(ref="base9")
    assert refs == ["base9"]


async def test_prepare_repo_conflicting_ref_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_REF", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo", lambda *a, **k: None
    )
    cfg = HackbotConfig(source=SourceConfig(repo_url="r", checkout_path=Path("/x")))
    hb = _hb(tmp_path, cfg)

    await hb.prepare_repo()  # prepares at the default ref first
    with pytest.raises(RuntimeError, match="already prepared"):
        await hb.prepare_repo(ref="base9")


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
    hb._source_base = hb._published_base = "basecommit"
    # prepare_repo would normally clone and set this; publish_changes only reads
    # repo_path and passes it to the (mocked) changes helpers, so a bare path is
    # enough here.
    hb._repo_path = tmp_path / "src"
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.collect",
        lambda repo, base, repo_url: ChangeSet(patch=b"x", metadata={"base": base}),
    )
    return hb


@pytest.mark.parametrize(
    ("action_type", "params"),
    [
        ("phabricator.submit_patch", {"bug_id": 1, "title": "Fix"}),
        ("phabricator.update_patch", {"revision_id": 42}),
    ],
)
def test_publish_changes_builds_phabricator_diff_when_action_recorded(
    tmp_path, monkeypatch, action_type, params
):
    hb = _hb_with_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff",
        lambda repo, base, repo_url: {
            "diff": {"changes": [], "sourceControlBaseRevision": base},
            "local_commits": {"node": {"author": "A"}},
        },
    )
    hb.actions.record(action_type, params, reasoning="r")

    hb.publish_changes()

    # One artifact holds both the creatediff payload and the local:commits data.
    submission = json.loads(
        (
            tmp_path / "artifacts" / "local-test" / "changes" / "phabricator_diff.json"
        ).read_text()
    )
    assert submission["diff"]["sourceControlBaseRevision"] == "basecommit"
    assert submission["local_commits"]["node"]["author"] == "A"


def test_publish_changes_builds_try_push_when_action_recorded(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_try_push",
        lambda repo, base: {"base_commit": base, "patches": ["cGF0Y2g="]},
    )
    hb.actions.record(
        "try_server.push", {"tasks": ["build-linux64/opt"]}, reasoning="r"
    )

    hb.publish_changes()

    payload = json.loads(
        (
            tmp_path / "artifacts" / "local-test" / "changes" / "try_push.json"
        ).read_text()
    )
    assert payload["base_commit"] == "basecommit"


def test_try_push_uses_the_published_base_not_a_local_one(tmp_path, monkeypatch):
    # A stacked-revision checkout seeds local commits and re-records the source
    # base onto one of them (see revision.checkout_revision). Lando has to
    # resolve the base in its own clone, so it must still be given the commit
    # the checkout was fetched at — a local sha would look valid (40 hex chars)
    # and then fail at Lando.
    hb = _hb_with_source(tmp_path, monkeypatch)
    bases = {}

    def _try_push(repo, base):
        bases["try"] = base
        return {"base_commit": base, "patches": []}

    def _phabricator_diff(repo, base, repo_url):
        bases["diff"] = base
        return None

    monkeypatch.setattr("hackbot_runtime.context.changes.build_try_push", _try_push)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff", _phabricator_diff
    )
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.base_commit", lambda repo: "localseededsha"
    )
    hb.record_source_base()
    hb.actions.record("try_server.push", {"tasks": ["t"]}, reasoning="r")
    hb.actions.record("phabricator.update_patch", {"revision_id": 1}, reasoning="r")

    hb.publish_changes()

    assert bases["try"] == "basecommit"
    # The submitted diff still measures from the seeded commit, so it covers
    # only the revision plus the agent's edits.
    assert bases["diff"] == "localseededsha"


def test_publish_changes_skips_try_push_without_action(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_try_push",
        lambda *a, **k: called.append(a) or {},
    )
    hb.actions.record("bugzilla.add_comment", {"bug_id": 1}, reasoning="r")

    hb.publish_changes()

    assert called == []
    assert not (
        tmp_path / "artifacts" / "local-test" / "changes" / "try_push.json"
    ).exists()


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
