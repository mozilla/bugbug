import asyncio
import json
import subprocess
from types import SimpleNamespace

from hackbot_agents.test_repair import agent
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)


def _result_msg(is_error=False):
    return SimpleNamespace(
        is_error=is_error,
        total_cost_usd=0.1,
        num_turns=3,
        result="max turns" if is_error else None,
        subtype=None,
    )


def _git_repo(path):
    """A two-commit repo standing in for the pinned shallow checkout.

    The culprit is validated with ``git rev-parse``, so the tests need real
    commits. Returns [base, head].
    """

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    shas = []
    for name in ("base", "head"):
        (path / f"{name}.txt").write_text(name)
        git("add", "-A")
        git("commit", "-qm", name)
        shas.append(git("rev-parse", "HEAD"))
    return shas


def _investigation(head, base, complete=True):
    return Investigation(
        project="autoland",
        hg_revision="hgrev",
        harness="mochitest",
        debug_build=False,
        failing_groups=[
            FailingGroup("dom/base/test/mochitest.ini", "dom/base/test/a.js")
        ],
        last_green_revision="greenhg",
        commit_range=CommitRange(head=head, base=base, span=2, complete=complete),
    )


def _fx_ctx(tmp_path):
    return SimpleNamespace(mozconfig=tmp_path / ".mozconfig", objdir=tmp_path / "obj")


def _run(tmp_path, verdicts, monkeypatch, complete=True, results=None):
    repo = tmp_path / "src"
    repo.mkdir()
    base, head = _git_repo(repo)
    scratch_out = tmp_path / "out"
    scratch_out.mkdir()
    calls = []

    async def fake_session(reporter, options, prompt):
        calls.append(prompt)
        verdict = verdicts.pop(0)
        if verdict is not None:
            if verdict.get("culprit_commit") == "HEAD":
                verdict = {**verdict, "culprit_commit": head}
            (scratch_out / "verdict.json").write_text(json.dumps(verdict))
        (scratch_out / "summary.md").write_text("the verdict")
        (scratch_out / "analysis.md").write_text("the reasoning")
        return _result_msg(is_error=bool(results and results.pop(0)))

    monkeypatch.setattr(agent, "_run_session", fake_session)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {"type": "sdk"})

    result = asyncio.run(
        agent.run_test_repair(
            bugzilla_mcp_server=None,
            source_repo=repo,
            fx_ctx=_fx_ctx(tmp_path),
            investigation=_investigation(head, base if complete else None, complete),
            task_logs={},
            scratch_out=scratch_out,
            verbose=False,
            log=None,
        )
    )
    return result, calls, head


def test_culprit_runs_fix_stage(tmp_path, monkeypatch):
    result, calls, head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.9},
            {
                "recommendation": "land_fix",
                "culprit_commit": "HEAD",
                "confidence": 0.9,
                "proposed_patch": True,
            },
        ],
        monkeypatch,
    )
    assert len(calls) == 2  # analysis + fix
    assert result.classification == "regression"
    assert result.culprit_commit == head
    assert result.recommendation == "land_fix"
    assert result.proposed_patch is True
    assert result.last_green_revision == "greenhg"
    assert result.num_turns == 6
    # The fix stage can only verify anything if a mozconfig exists.
    assert (tmp_path / ".mozconfig").exists()


def test_no_culprit_skips_fix_stage(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": None, "confidence": 0.3}],
        monkeypatch,
    )
    assert len(calls) == 1  # no fix stage
    assert result.culprit_commit is None
    assert result.proposed_patch is False
    assert result.recommendation == "do_not_backout"


def test_hallucinated_culprit_is_discarded(tmp_path, monkeypatch):
    result, calls, _head = _run(
        tmp_path,
        [{"recommendation": "backout", "culprit_commit": "deadbeefdeadbeef"}],
        monkeypatch,
    )
    assert len(calls) == 1
    assert result.culprit_commit is None
    assert result.recommendation == "do_not_backout"


def test_fix_stage_verdict_rewrite_keeps_analysis_culprit(tmp_path, monkeypatch):
    # The fix stage may rewrite the whole file; the culprit must survive that.
    result, _calls, head = _run(
        tmp_path,
        [
            {
                "recommendation": "backout",
                "culprit_commit": "HEAD",
                "culprit_bug": 123,
                "confidence": 0.9,
            },
            {"recommendation": "land_fix", "proposed_patch": True},
        ],
        monkeypatch,
    )
    assert result.culprit_commit == head
    assert result.culprit_bug == 123
    assert result.confidence == 0.9
    assert result.recommendation == "land_fix"


def test_failed_fix_stage_still_publishes_analysis(tmp_path, monkeypatch):
    result, calls, head = _run(
        tmp_path,
        [
            {"recommendation": "backout", "culprit_commit": "HEAD", "confidence": 0.9},
            None,
        ],
        monkeypatch,
        results=[False, True],
    )
    assert len(calls) == 2
    assert result.culprit_commit == head
    assert result.recommendation == "backout"
    assert result.analysis == "the reasoning"


def test_complete_range_prompt_gives_a_base_anchored_range(tmp_path, monkeypatch):
    _result, calls, head = _run(tmp_path, [{"culprit_commit": None}], monkeypatch)
    assert "culprit is one of them" in calls[0]
    # The agent enumerates the range itself rather than being handed every sha.
    assert "git log --oneline" in calls[0]
    assert f"..{head}" in calls[0]


def test_incomplete_range_prompt_does_not_assert_the_culprit(tmp_path, monkeypatch):
    _result, calls, _head = _run(
        tmp_path, [{"culprit_commit": None}], monkeypatch, complete=False
    )
    assert "may predate" in calls[0]
    assert "culprit is one of them" not in calls[0]
    # Without a last-green base the range falls back to the clone depth.
    assert "HEAD~2..HEAD" in calls[0]


def test_assemble_defaults_on_empty_verdict(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={},
        source_repo=tmp_path,
        last_green_revision=None,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    # A regression is assumed, but there is no culprit to back out.
    assert result.classification == "regression"
    assert result.recommendation == "do_not_backout"


def test_assemble_tolerates_malformed_verdict_fields(tmp_path):
    # verdict.json is model-authored; bad fields must not crash a finished run.
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={
            "recommendation": "backout",
            "culprit_commit": "abc",
            "confidence": "high",
            "culprit_bug": "n/a",
        },
        source_repo=tmp_path,
        last_green_revision=None,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    assert result.confidence == 0.0
    assert result.culprit_bug is None
    assert result.classification == "regression"
    # "abc" resolves to no commit in the checkout, so the blame is dropped.
    assert result.culprit_commit is None


def test_assemble_reports_intermittent_classification(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = agent._assemble_result(
        out,
        verdict={"classification": "intermittent", "intermittent_bug": 42},
        source_repo=tmp_path,
        last_green_revision=None,
        total_turns=1,
        total_cost=0.0,
        publish_file=None,
    )
    assert result.classification == "intermittent"
    assert result.intermittent_bug == 42
    assert result.recommendation == "do_not_backout"


def test_coerce_recommendation_defaults_by_classification():
    assert agent._coerce_recommendation("bogus", "regression", True) == "backout"
    assert (
        agent._coerce_recommendation("bogus", "intermittent", False) == "do_not_backout"
    )
    assert agent._coerce_recommendation("land_fix", "regression", True) == "land_fix"
    # "backout" is meaningless without a commit to back out.
    assert agent._coerce_recommendation("backout", "regression", False) == (
        "do_not_backout"
    )


def test_resolve_culprit_normalizes_against_the_checkout(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    _base, head = _git_repo(repo)
    assert agent._resolve_culprit(repo, head) == head
    assert agent._resolve_culprit(repo, head[:8]) == head
    assert agent._resolve_culprit(repo, "deadbeefdeadbeefdeadbeef") is None
    assert agent._resolve_culprit(repo, None) is None
    assert agent._resolve_culprit(repo, "  ") is None


def test_range_expr_prefers_the_last_green_base():
    anchored = CommitRange(head="h" * 40, base="b" * 40, span=5, complete=True)
    assert agent._range_expr(anchored) == f"{'b' * 40}..{'h' * 40}"
    assert agent._range_expr(CommitRange("h" * 40, None, 5, False)) == "HEAD~5..HEAD"
