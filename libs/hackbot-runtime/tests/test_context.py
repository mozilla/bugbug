"""Tests for HackbotContext capabilities and results plumbing."""

from pathlib import Path

import pytest
from hackbot_runtime import BaseAgentInputs, HackbotContext
from hackbot_runtime.config import FirefoxConfig, HackbotConfig, SourceConfig


def _hb(tmp_path, config: HackbotConfig) -> HackbotContext:
    hb = HackbotContext(run_id="local-test", artifacts_dir=tmp_path / "artifacts")
    hb._config = config
    return hb


class _SampleInputs(BaseAgentInputs):
    bug_id: int
    model: str | None = None


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

    def fake_ensure(path: Path, repo_url: str, ref: str | None = None) -> None:
        calls.append((path, repo_url, ref))

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
    assert calls == [(tmp_path / "from-env", "https://example.com/r.git", None)]


def test_source_repo_honors_source_ref_env(tmp_path, monkeypatch):
    calls = []

    def fake_ensure(path: Path, repo_url: str, ref: str | None = None) -> None:
        calls.append((path, repo_url, ref))

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.setenv("SOURCE_REF", "deadbeef")

    cfg = HackbotConfig(
        source=SourceConfig(repo_url="r", checkout_path=Path("/from/toml"))
    )
    hb = _hb(tmp_path, cfg)

    assert hb.source_repo == Path("/from/toml")
    assert calls == [(Path("/from/toml"), "r", "deadbeef")]


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


def test_load_inputs_without_url_reads_env(tmp_path, monkeypatch):
    # Local/docker path: no RUN_INPUTS_URL, so inputs come from the environment.
    monkeypatch.setenv("BUG_ID", "42")
    monkeypatch.setenv("MODEL", "claude-opus")
    hb = _hb(tmp_path, HackbotConfig())
    assert hb.run_inputs_url is None

    inputs = hb.load_inputs(_SampleInputs)

    assert inputs.bug_id == 42
    assert inputs.model == "claude-opus"


def test_load_inputs_uses_remote_config(tmp_path, monkeypatch):
    # Production path: the required field is supplied by the fetched file, not env.
    monkeypatch.delenv("BUG_ID", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.load_remote_config",
        lambda url: {"bug_id": 7, "model": "from-config"},
    )
    hb = _hb(tmp_path, HackbotConfig())
    hb.run_inputs_url = "https://signed.example/inputs.json"

    inputs = hb.load_inputs(_SampleInputs)

    assert inputs.bug_id == 7
    assert inputs.model == "from-config"


def test_load_inputs_env_overrides_remote_config(tmp_path, monkeypatch):
    # An env var wins over the same key in the config, while keys absent from the
    # environment still fall through to the config.
    monkeypatch.setenv("MODEL", "from-env")
    monkeypatch.delenv("BUG_ID", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.load_remote_config",
        lambda url: {"bug_id": 7, "model": "from-config"},
    )
    hb = _hb(tmp_path, HackbotConfig())
    hb.run_inputs_url = "https://signed.example/inputs.json"

    inputs = hb.load_inputs(_SampleInputs)

    assert inputs.model == "from-env"  # env overrides config
    assert inputs.bug_id == 7  # config supplies what env doesn't
