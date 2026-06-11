"""Tests for HackbotContext capabilities and results plumbing."""

from pathlib import Path

import pytest
from hackbot_runtime import HackbotContext
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

    def fake_ensure(path: Path, repo_url: str) -> None:
        calls.append((path, repo_url))

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.setenv("SOURCE_REPO", str(tmp_path / "from-env"))

    cfg = HackbotConfig(
        source=SourceConfig(
            repo_url="https://example.com/r.git",
            checkout_path=Path("/from/toml"),
        )
    )
    hb = _hb(tmp_path, cfg)

    assert hb.source_repo == tmp_path / "from-env"
    assert calls == [(tmp_path / "from-env", "https://example.com/r.git")]


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
