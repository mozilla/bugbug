import asyncio
import json
from types import SimpleNamespace

from hackbot_agents.test_repair import agent
from hackbot_agents.test_repair.resolve import FailingGroup, Investigation


def _result_msg():
    return SimpleNamespace(
        is_error=False, total_cost_usd=0.1, num_turns=3, result=None, subtype=None
    )


def _investigation():
    return Investigation(
        project="autoland",
        hg_revision="hgrev",
        harness="mochitest",
        failing_groups=[
            FailingGroup("dom/base/test/mochitest.ini", "dom/base/test/a.js")
        ],
        last_green_revision="greensha",
        candidate_commits=["headsha", "oldsha"],
    )


def _run(tmp_path, verdicts, monkeypatch):
    scratch_out = tmp_path / "out"
    scratch_out.mkdir()
    calls = []

    async def fake_session(reporter, options, prompt):
        calls.append(prompt)
        verdict = verdicts.pop(0)
        (scratch_out / "verdict.json").write_text(json.dumps(verdict))
        (scratch_out / "summary.md").write_text("the verdict")
        (scratch_out / "analysis.md").write_text("the reasoning")
        return _result_msg()

    monkeypatch.setattr(agent, "_run_session", fake_session)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {"type": "sdk"})

    result = asyncio.run(
        agent.run_test_repair(
            bugzilla_mcp_server=None,
            source_repo=tmp_path,
            fx_ctx=object(),
            investigation=_investigation(),
            task_logs={},
            scratch_out=scratch_out,
            verbose=False,
            log=None,
        )
    )
    return result, calls


def test_culprit_runs_fix_stage(tmp_path, monkeypatch):
    result, calls = _run(
        tmp_path,
        [
            {
                "recommendation": "backout",
                "culprit_commit": "headsha",
                "confidence": 0.9,
            },
            {
                "recommendation": "land_fix",
                "culprit_commit": "headsha",
                "confidence": 0.9,
                "proposed_patch": True,
            },
        ],
        monkeypatch,
    )
    assert len(calls) == 2  # analysis + fix
    assert result.classification == "regression"
    assert result.culprit_commit == "headsha"
    assert result.recommendation == "land_fix"
    assert result.proposed_patch is True
    assert result.last_green_revision == "greensha"
    assert result.num_turns == 6


def test_no_culprit_skips_fix_stage(tmp_path, monkeypatch):
    result, calls = _run(
        tmp_path,
        [
            {
                "recommendation": "backout",
                "culprit_commit": None,
                "confidence": 0.3,
            },
        ],
        monkeypatch,
    )
    assert len(calls) == 1  # no fix stage
    assert result.culprit_commit is None
    assert result.proposed_patch is False


def test_assemble_defaults_on_missing_verdict(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        last_green_revision=None,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    # The agent assumes a regression; a missing verdict defaults accordingly.
    assert result.classification == "regression"
    assert result.recommendation == "backout"


def test_assemble_tolerates_malformed_verdict_fields(tmp_path):
    # verdict.json is authored by the model; bad confidence/bug must not crash the
    # run after both stages have already done the expensive work.
    out = tmp_path / "out"
    out.mkdir()
    (out / "verdict.json").write_text(
        json.dumps(
            {
                "recommendation": "backout",
                "culprit_commit": "abc",
                "confidence": "high",
                "culprit_bug": "n/a",
            }
        )
    )
    result = agent._assemble_result(
        out,
        last_green_revision=None,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    assert result.confidence == 0.0
    assert result.culprit_bug is None
    assert result.classification == "regression"
    assert result.culprit_commit == "abc"


def test_coerce_recommendation_defaults_by_classification():
    assert agent._coerce_recommendation("bogus", "regression") == "backout"
    assert agent._coerce_recommendation("bogus", "intermittent") == "do_not_backout"
    assert agent._coerce_recommendation("land_fix", "regression") == "land_fix"
