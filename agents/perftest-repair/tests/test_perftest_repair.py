import json
import string
import subprocess
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from hackbot_agents.perftest_repair import agent, recent
from hackbot_agents.perftest_repair.__main__ import AgentInputs
from hackbot_agents.perftest_repair.agent import (
    PROMPTS,
    RULES,
    PerftestRepairResult,
    assemble_result,
    render_prompt,
)
from hackbot_agents.perftest_repair.notify import build_message, try_push_of
from hackbot_runtime import ActionsRecorder
from pydantic import ValidationError


def _result_msg(**kwargs):
    return SimpleNamespace(**{"num_turns": 7, "total_cost_usd": 1.5, **kwargs})


def _assemble(tmp_path, source_repo=None):
    return assemble_result(
        bug=123,
        scratch_out=tmp_path,
        result_msg=_result_msg(),
        source_repo=source_repo,
    )


def test_broker_url_required(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    with pytest.raises(ValidationError, match="broker_url"):
        AgentInputs(bug_id=1)


def test_mcp_urls_derived_from_broker_url():
    inputs = AgentInputs(bug_id=1, broker_url="http://broker:8765/")
    assert inputs.bugzilla_mcp_url == "http://broker:8765/bugzilla/mcp"


def test_empty_env_inputs_fall_back_to_defaults(monkeypatch):
    # Compose passes unset optional inputs as empty strings.
    monkeypatch.setenv("SKIP_FIREFOX_BUILD", "")
    monkeypatch.setenv("MODEL", "")
    inputs = AgentInputs(bug_id=1, broker_url="http://broker")
    assert inputs.skip_firefox_build is True
    assert inputs.model is None


@pytest.mark.parametrize("verify", ["verify-skipped.md", "verify-local.md"])
def test_investigate_prompt_renders(tmp_path, verify):
    prompt = render_prompt(
        "investigate-and-fix.md",
        bug_id=4242,
        rules_dir=RULES,
        scratch_out=tmp_path,
        verify_step=render_prompt(verify),
        stop_step=render_prompt("stop-conditions.md"),
        try_rebuild_intermittent=10,
        try_rebuild_permafail=3,
        try_ref="try",
    )
    assert "Bug to work on: 4242" in prompt
    assert f"{tmp_path}/logs" in prompt
    assert '"failure_type"' in prompt


def test_prompts_only_use_known_fields():
    known = {
        "bug_id",
        "rules_dir",
        "scratch_out",
        "source_dir",
        "stop_step",
        "try_rebuild_intermittent",
        "try_rebuild_permafail",
        "try_ref",
        "verify_step",
    }
    for path in PROMPTS.glob("*.md"):
        fields = {name for _, name, _, _ in string.Formatter().parse(path.read_text())}
        assert fields - {None} <= known, path.name


def test_system_prompt_names_the_checkout():
    system = render_prompt("system.md", rules_dir=RULES, source_dir="/work/ff")
    assert "`/work/ff`" in system


def test_system_prompt_names_every_ruleset():
    system = render_prompt("system.md", rules_dir=RULES, source_dir="/src")
    rulesets = {p.name for p in RULES.glob("*.md")} - {"README.md"}
    assert rulesets
    for name in rulesets:
        assert name in system


def test_result_from_verdict(tmp_path):
    (tmp_path / "verdict.json").write_text(
        json.dumps(
            {
                "failure_type": "permafail",
                "harness": "raptor",
                "cause": "harness",
                "regressor_bug": "1900000",
                "confidence": 0.8,
            }
        )
    )
    (tmp_path / "summary.md").write_text("raptor fails")
    result = _assemble(tmp_path)
    assert result.failure_type == "permafail"
    assert result.harness == "raptor"
    assert result.cause == "harness"
    assert result.regressor_bug == 1900000
    assert result.confidence == 0.8
    assert result.summary == "raptor fails"
    assert result.outcome == "diagnosis"
    assert result.num_turns == 7


def test_result_coerces_bad_verdict(tmp_path):
    (tmp_path / "verdict.json").write_text(
        json.dumps({"failure_type": "flaky", "harness": 3, "confidence": 7})
    )
    result = _assemble(tmp_path)
    assert result.failure_type == "unknown"
    assert result.harness == "other"
    assert result.cause == "unknown"
    assert result.confidence == 1.0


def test_result_without_verdict(tmp_path):
    result = _assemble(tmp_path)
    assert result.failure_type == "unknown"
    assert result.summary == ""


def test_outcome_comes_from_the_working_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    out = tmp_path / "out"
    out.mkdir()
    assert _assemble(out, source_repo=repo).outcome == "diagnosis"

    (repo / "parse.py").write_text("fixed\n")
    assert _assemble(out, source_repo=repo).outcome == "fix"


def _result(**kwargs):
    return PerftestRepairResult(
        **{
            "bug_id": 2076647,
            "harness": "mozperftest",
            "failure_type": "intermittent",
            "cause": "harness",
            "confidence": 0.93,
            "summary": "# Summary\n\nThe parser splits a line.",
            "num_turns": 7,
            "total_cost_usd": 1.5,
            **kwargs,
        }
    )


def test_slack_message_for_a_fix():
    message = build_message(_result(outcome="fix"), run_id="r1")
    assert message.startswith(
        "*<https://bugzilla.mozilla.org/show_bug.cgi?id=2076647|Bug 2076647>: "
        "Suggested fix (unverified)*"
    )
    assert "mozperftest · intermittent · cause: harness · confidence 93%" in message
    # The markdown heading is dropped; the prose is kept.
    assert "# Summary" not in message
    assert "The parser splits a line." in message
    assert "/runs/r1|Hackbot run>: the patch and analysis" in message


def test_slack_message_for_a_diagnosis_truncates_long_summaries():
    message = build_message(
        _result(outcome="diagnosis", summary="x" * 5000), run_id=None
    )
    assert "Diagnosis, no fix suggested" in message
    assert "x" * 1200 + "…" in message
    assert "Hackbot run" not in message


_TRY_PUSH = {
    "tasks": ["perftest-android-hw-a55-background-resource-fenix"],
    "rebuild": 10,
}


def test_slack_message_links_the_try_push():
    message = build_message(_result(outcome="fix"), run_id="r1", try_push=_TRY_PUSH)
    assert "Suggested fix, pushed to try*" in message
    assert (
        "Try push (10× `perftest-android-hw-a55-background-resource-fenix`): "
        "{{actions.try.url}}"
    ) in message


def test_slack_message_drops_a_try_push_without_a_fix():
    message = build_message(
        _result(outcome="diagnosis"), run_id="r1", try_push=_TRY_PUSH
    )
    assert "Try push" not in message
    assert "{{actions" not in message


def test_try_push_of_finds_the_push_recorded_under_the_try_ref(tmp_path):
    recorder = ActionsRecorder(artifacts_dir=tmp_path)
    assert try_push_of(recorder) is None
    recorder.record("try_server.push", {"tasks": ["other"]}, ref="other")
    assert try_push_of(recorder) is None
    recorder.record("try_server.push", _TRY_PUSH, ref="try")
    assert try_push_of(recorder) == _TRY_PUSH


def test_stop_conditions_apply_unless_ignored(monkeypatch):
    assert "the bug is closed" in render_prompt("stop-conditions.md")
    assert "investigate the bug fully" in render_prompt("stop-conditions-ignored.md")
    monkeypatch.delenv("IGNORE_STOP_CONDITIONS", raising=False)
    assert AgentInputs(bug_id=1, broker_url="http://b").ignore_stop_conditions is False


def test_investigate_prompt_gives_both_rebuild_counts(tmp_path):
    prompt = render_prompt(
        "investigate-and-fix.md",
        bug_id=1,
        rules_dir=RULES,
        scratch_out=tmp_path,
        verify_step="",
        stop_step="",
        try_rebuild_intermittent=10,
        try_rebuild_permafail=3,
        try_ref="try",
    )
    assert "`rebuild=3` for a permafail" in prompt
    assert "`rebuild=10` for an intermittent" in prompt
    assert "every distinct failing job and platform" in prompt


def _mock_treeherder(monkeypatch, handler):
    real_client = recent.httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(recent.httpx, "AsyncClient", client)


async def test_recent_failures_counts_the_last_week(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=[{"job_id": 1}, {"job_id": 2}])

    _mock_treeherder(monkeypatch, handler)
    assert await recent.recent_failures(1966101) == 2
    assert seen["bug"] == "1966101"
    assert seen["tree"] == "all"
    span = date.fromisoformat(seen["endday"]) - date.fromisoformat(seen["startday"])
    assert span.days == recent.RECENT_DAYS


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={}),
    ],
)
async def test_recent_failures_is_unknown_when_treeherder_cannot_answer(
    monkeypatch, response
):
    _mock_treeherder(monkeypatch, lambda request: response)
    assert await recent.recent_failures(1) is None


async def _run(monkeypatch, tmp_path, failures, **kwargs):
    async def fake_recent_failures(bug):
        return failures

    async def broker_reached(*args):
        raise RuntimeError("session started")

    monkeypatch.setattr(agent, "recent_failures", fake_recent_failures)
    monkeypatch.setattr(agent, "check_broker", broker_reached)
    return await agent.run_perftest_repair(
        bugzilla_mcp_server={"type": "http", "url": "http://broker"},
        source_repo=tmp_path,
        fx_ctx=None,
        bug=2077400,
        scratch_out=tmp_path,
        **kwargs,
    )


async def test_a_bug_without_recent_failures_is_skipped(monkeypatch, tmp_path):
    result = await _run(monkeypatch, tmp_path, failures=0)
    assert result.outcome == "skipped"
    assert "No failures in the last 7 days" in result.summary
    assert result.num_turns == 0


@pytest.mark.parametrize("failures", [3, None])
async def test_a_failing_or_unknown_bug_is_investigated(
    monkeypatch, tmp_path, failures
):
    with pytest.raises(RuntimeError, match="session started"):
        await _run(monkeypatch, tmp_path, failures=failures)


async def test_test_runs_investigate_a_bug_without_recent_failures(
    monkeypatch, tmp_path
):
    with pytest.raises(RuntimeError, match="session started"):
        await _run(monkeypatch, tmp_path, failures=0, ignore_stop_conditions=True)
