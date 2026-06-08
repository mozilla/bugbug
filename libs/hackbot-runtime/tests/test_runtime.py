"""Tests for summary persistence when no uploader is configured."""

import json

from hackbot_runtime import AgentResult, Context
from hackbot_runtime.runtime import _finish


def test_run_id_defaults_to_unique_generated_id(monkeypatch):
    monkeypatch.delenv("RUN_ID", raising=False)
    a, b = Context(), Context()
    assert a.run_id != b.run_id
    assert a.run_id.startswith("local-")


def test_run_id_env_overrides_default(monkeypatch):
    monkeypatch.setenv("RUN_ID", "orchestrator-42")
    assert Context().run_id == "orchestrator-42"


def _ctx(tmp_path, run_id="local-test"):
    # No results_policy_url -> uploader is None -> local artifacts path.
    return Context(run_id=run_id, artifacts_dir=tmp_path / "artifacts")


def test_summary_written_locally_without_uploader(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.actions.record(
        "bugzilla.update_bug",
        {"bug_id": 1, "changes": {"severity": "S2"}},
        reasoning="rule X",
    )

    code = _finish(ctx, AgentResult(status="ok", findings={"bugs_processed": 1}))

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


def test_runs_are_namespaced_by_run_id(tmp_path):
    ctx_a = _ctx(tmp_path, run_id="run-a")
    ctx_b = _ctx(tmp_path, run_id="run-b")
    _finish(ctx_a, AgentResult(status="ok"))
    _finish(ctx_b, AgentResult(status="error", error="x"))

    base = tmp_path / "artifacts"
    assert json.loads((base / "run-a" / "summary.json").read_text())["status"] == "ok"
    assert (
        json.loads((base / "run-b" / "summary.json").read_text())["status"] == "error"
    )


def test_publish_file_copies_locally_without_uploader(tmp_path):
    ctx = _ctx(tmp_path)
    log = tmp_path / "agent.log"
    log.write_text("hello log")

    key = ctx.publish_file("logs/agent.log", log)

    assert key == "logs/agent.log"
    written = tmp_path / "artifacts" / "local-test" / "logs" / "agent.log"
    assert written.read_text() == "hello log"
