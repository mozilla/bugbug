"""Tests for ``run_uplift``, the orchestration around the agent session.

``run_session`` is stood in for, so these cover the assembly around it: the
servers and options the session is handed, and what the run makes of the tree
and report it leaves behind. The stand-in commits into a real checkout, like a
real session is told to, so the mechanical checks run rather than being
bypassed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from hackbot_agents.uplift_merge_conflict_resolver import agent
from hackbot_agents.uplift_merge_conflict_resolver.models import GitSource
from hackbot_runtime import AgentError

BUGBUG_MCP = {"type": "http", "url": "http://localhost:8080/mcp"}

REPORT = {
    "resolved": True,
    "confidence": "medium",
    "summary": "Rebased the import block.",
    "conflicts": [{"file": "a.cpp", "resolution": "kept both edits"}],
    "unresolved": [],
}


def fake_session(
    git_in, report: dict | None, *, commits: bool = True, dirty: bool = False
):
    """A ``run_session`` stand-in that resolves, commits, and writes ``report``.

    The scratch directory is read off the options, which is how the real agent
    learns it too. ``commits`` and ``dirty`` produce the two ways a session can
    claim success while leaving the checkout unfit to land.
    """
    captured: dict = {}

    async def session(reporter, options, prompt):
        captured["options"] = options
        captured["prompt"] = prompt
        scratch_out = Path(options.add_dirs[0])
        if report is not None:
            (scratch_out / "report.json").write_text(json.dumps(report))
            (scratch_out / "summary.md").write_text("# resolved")
        repo = Path(options.cwd)
        if commits:
            (repo / "f.txt").write_text("line1\nline2 uplifted\nline3\n")
            git_in(repo, "add", "-A")
            git_in(repo, "commit", "-qm", "uplift the patch")
        if dirty:
            (repo / "f.txt").write_text("line1\nline2 forgotten\nline3\n")
        return SimpleNamespace(
            is_error=False,
            result="done",
            subtype="success",
            num_turns=4,
            total_cost_usd=0.25,
        )

    return session, captured


async def run(monkeypatch, session, repo, **overrides):
    kwargs = dict(
        bugbug_mcp_server=BUGBUG_MCP,
        broker_url="http://uplift-broker:8765",
        source_repo=repo,
        target_branch="beta",
        sources=[GitSource(commit="a" * 40)],
    )
    kwargs.update(overrides)
    monkeypatch.setattr(agent, "run_session", session)
    return await agent.run_uplift(**kwargs)


async def test_run_uplift_reports_what_the_session_resolved(
    monkeypatch, publisher, repo, git_in
):
    session, _ = fake_session(git_in, REPORT)

    result = await run(monkeypatch, session, repo, publish_file=publisher)

    assert (result.resolved, result.confidence) == (True, "medium"), (
        "The report the session wrote should decide the run's outcome."
    )
    assert result.verification_failures == [], (
        "A session that committed a clean resolution has nothing to flag."
    )
    assert result.target_branch == "beta", "The run should name the branch it targeted."
    assert result.base_commit == git_in(repo, "rev-parse", "HEAD~"), (
        "The commit the patches were applied onto is what makes a run "
        "reproducible, since a branch name moves."
    )
    assert [entry.source for entry in result.requested_sources] == [
        {"kind": "git", "commit": "a" * 40}
    ], "The result should record which sources were requested, not just how many."
    assert (result.num_turns, result.total_cost_usd) == (4, 0.25), (
        "The session's run metadata should reach the result."
    )
    assert [conflict.file for conflict in result.conflicts] == ["a.cpp"], (
        "Per-file conflict resolutions should reach the result."
    )
    assert publisher.names == [
        "summary.md",
        "report.unverified.json",
        "report.json",
    ], (
        "What the agent wrote is published, and `report.json` beside it is the "
        "verified one a consumer reads."
    )


async def test_run_uplift_refuses_a_checkout_that_is_not_the_pinned_commit(
    monkeypatch, repo, git_in
):
    session, _ = fake_session(git_in, REPORT)

    with pytest.raises(AgentError, match="not the requested"):
        await run(monkeypatch, session, repo, target_commit="f" * 40)


async def test_run_uplift_wires_every_mozilla_mcp_server(monkeypatch, repo, git_in):
    session, captured = fake_session(git_in, REPORT)

    await run(monkeypatch, session, repo)

    assert set(captured["options"].mcp_servers) == {
        "bugbug",
        "searchfox",
        "mozilla_vcs",
    }, "The session should get the bugbug server plus the in-process Mozilla ones."
    assert "beta" in captured["prompt"], (
        "The session's prompt should name the branch being uplifted onto."
    )


async def test_run_uplift_refuses_to_call_a_dirty_tree_resolved(
    monkeypatch, repo, git_in
):
    session, _ = fake_session(git_in, REPORT, dirty=True)

    result = await run(monkeypatch, session, repo)

    assert result.resolved is False, (
        "Uncommitted work collapses the stack, so the claim is overruled."
    )
    assert any("uncommitted" in failure for failure in result.verification_failures), (
        "The reason for overruling the claim should be recorded on the result."
    )


async def test_the_published_report_agrees_with_the_verified_result(
    monkeypatch, publisher, repo, git_in
):
    """An overruled claim must not survive in the report a consumer reads."""
    session, _ = fake_session(git_in, REPORT, dirty=True)

    result = await run(monkeypatch, session, repo, publish_file=publisher)

    assert result.resolved is False, "The checks overruled the claim."
    assert json.loads(publisher.bodies["report.json"]) == {
        "resolved": False,
        "confidence": "medium",
        "summary": REPORT["summary"],
        "conflicts": REPORT["conflicts"],
        "unresolved": [],
        "verification_failures": result.verification_failures,
    }, "`report.json` should carry the verified outcome and why it was overruled."
    assert json.loads(publisher.bodies["report.unverified.json"])["resolved"] is True, (
        "The agent's own claim is kept verbatim, under a name that says so."
    )


async def test_run_uplift_refuses_a_resolution_with_no_commits(
    monkeypatch, repo, git_in
):
    session, _ = fake_session(git_in, REPORT, commits=False)

    result = await run(monkeypatch, session, repo)

    assert result.resolved is False, (
        "A claimed resolution with no commits has no patch behind it."
    )
    assert any("no commits" in failure for failure in result.verification_failures), (
        "The result should say why the claim was not accepted."
    )


async def test_run_uplift_survives_a_session_that_wrote_no_report(
    monkeypatch, repo, git_in
):
    session, _ = fake_session(git_in, None)

    result = await run(monkeypatch, session, repo)

    assert result.resolved is False, (
        "A session that wrote no report is an unresolved run, not a crash."
    )


async def test_run_uplift_refuses_a_run_with_no_sources(monkeypatch, repo, git_in):
    session, _ = fake_session(git_in, REPORT)

    with pytest.raises(AgentError, match="no sources"):
        await run(monkeypatch, session, repo, sources=[])
