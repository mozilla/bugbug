"""Tests for summary persistence when no uploader is configured."""

import json

import pytest
from hackbot_runtime import AgentError, HackbotContext, run_async
from hackbot_runtime.runtime import _discover_config_path, _finish, _resolve_config


def test_run_id_defaults_to_unique_generated_id(monkeypatch):
    monkeypatch.delenv("RUN_ID", raising=False)
    a, b = HackbotContext(), HackbotContext()
    assert a.run_id != b.run_id
    assert a.run_id.startswith("local-")


def test_run_id_env_overrides_default(monkeypatch):
    monkeypatch.setenv("RUN_ID", "orchestrator-42")
    assert HackbotContext().run_id == "orchestrator-42"


def _ctx(tmp_path, run_id="local-test"):
    # No results_policy_url -> uploader is None -> local artifacts path.
    return HackbotContext(run_id=run_id, artifacts_dir=tmp_path / "artifacts")


def test_summary_written_locally_without_uploader(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.actions.record(
        "bugzilla.update_bug",
        {"bug_id": 1, "changes": {"severity": "S2"}},
        reasoning="rule X",
    )

    code = _finish(ctx, {"bugs_processed": 1})

    assert code == 0
    # Written under the per-run subdir: artifacts_dir / run_id.
    summary = json.loads(
        (tmp_path / "artifacts" / "local-test" / "summary.json").read_text()
    )
    assert summary["status"] == "ok"
    assert summary["findings"] == {"bugs_processed": 1}
    assert summary["actions"][0]["type"] == "bugzilla.update_bug"


def test_summary_written_for_exception(tmp_path):
    ctx = _ctx(tmp_path)
    code = _finish(ctx, RuntimeError("boom"))

    assert code == 1
    summary = json.loads(
        (tmp_path / "artifacts" / "local-test" / "summary.json").read_text()
    )
    assert summary["status"] == "error"
    assert "boom" in summary["error"]


def test_non_dict_return_is_contract_error(tmp_path):
    ctx = _ctx(tmp_path)
    code = _finish(ctx, "not a dict")

    assert code == 1
    summary = json.loads(
        (tmp_path / "artifacts" / "local-test" / "summary.json").read_text()
    )
    assert summary["status"] == "error"
    assert "expected a findings dict" in summary["error"]


def test_runs_are_namespaced_by_run_id(tmp_path):
    ctx_a = _ctx(tmp_path, run_id="run-a")
    ctx_b = _ctx(tmp_path, run_id="run-b")
    _finish(ctx_a, None)
    _finish(ctx_b, RuntimeError("x"))

    base = tmp_path / "artifacts"
    assert json.loads((base / "run-a" / "summary.json").read_text())["status"] == "ok"
    assert (
        json.loads((base / "run-b" / "summary.json").read_text())["status"] == "error"
    )


def _dummy_entry(ctx):  # stand-in entrypoint for discovery tests
    return None


def test_config_auto_discovered_from_cwd(tmp_path, monkeypatch):
    (tmp_path / "hackbot.toml").write_text('[source]\nrepo_url = "https://x/y.git"\n')
    monkeypatch.chdir(tmp_path)

    assert _discover_config_path(_dummy_entry) == tmp_path / "hackbot.toml"
    cfg = _resolve_config(_dummy_entry, None)
    assert cfg.source is not None
    assert cfg.source.repo_url == "https://x/y.git"


def test_config_discovered_above_entrypoint_module(tmp_path, monkeypatch):
    # Agent root holds hackbot.toml; the entry module lives below it (editable
    # checkout). cwd has no toml, so discovery must walk up from the module.
    agent_root = tmp_path / "agent"
    pkg = agent_root / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "agent.py").write_text("def main(ctx):\n    return None\n")
    (agent_root / "hackbot.toml").write_text('[source]\nrepo_url = "https://a/b.git"\n')

    empty = tmp_path / "elsewhere"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.syspath_prepend(str(agent_root))
    from mypkg.agent import main  # type: ignore

    assert _discover_config_path(main) == agent_root / "hackbot.toml"


def test_no_config_discovered_yields_empty(tmp_path, monkeypatch):
    pkg = tmp_path / "barepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "agent.py").write_text("def main(ctx):\n    return None\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    from barepkg.agent import main  # type: ignore

    assert _discover_config_path(main) is None
    cfg = _resolve_config(main, None)
    assert cfg.source is None and cfg.firefox is None


def _run_env(tmp_path, monkeypatch):
    # Make run_async write into tmp and discover no hackbot.toml.
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("RUN_ID", "t")
    monkeypatch.delenv("RESULTS_POLICY_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no hackbot.toml here


def test_run_async_exits_zero_and_writes_summary(tmp_path, monkeypatch):
    _run_env(tmp_path, monkeypatch)

    async def main(ctx):
        return {"did": "work"}

    with pytest.raises(SystemExit) as exc:
        run_async(main)

    assert exc.value.code == 0
    summary = json.loads((tmp_path / "t" / "summary.json").read_text())
    assert summary["status"] == "ok"
    assert summary["findings"] == {"did": "work"}


def test_run_async_exits_nonzero_when_agent_raises(tmp_path, monkeypatch):
    _run_env(tmp_path, monkeypatch)

    async def main(ctx):
        raise AgentError("nope")

    with pytest.raises(SystemExit) as exc:
        run_async(main)

    assert exc.value.code == 1
    summary = json.loads((tmp_path / "t" / "summary.json").read_text())
    assert summary["status"] == "error"
    assert "nope" in summary["error"]


def test_publish_file_copies_locally_without_uploader(tmp_path):
    ctx = _ctx(tmp_path)
    log = tmp_path / "agent.log"
    log.write_text("hello log")

    key = ctx.publish_file("logs/agent.log", log)

    assert key == "logs/agent.log"
    written = tmp_path / "artifacts" / "local-test" / "logs" / "agent.log"
    assert written.read_text() == "hello log"
