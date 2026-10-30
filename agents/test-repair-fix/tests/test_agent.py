import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest
from hackbot_agents.test_repair.agent import TestRepairResult
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)
from hackbot_agents.test_repair_fix import agent
from hackbot_agents.test_repair_fix.config import BUILD_TOOL, SKIP_FIREFOX_BUILD
from hackbot_runtime import ActionsRecorder, AgentError

ACTION_TOOLS = {"mcp__actions__phabricator_submit_patch"}


def _result_msg(is_error=False):
    return SimpleNamespace(
        is_error=is_error,
        total_cost_usd=0.1,
        num_turns=3,
        result="max turns" if is_error else None,
        subtype=None,
    )


def _flat(text):
    return " ".join(text.split())


def _git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _culprit_repo(path, message="Bug 555 - break it r=me"):
    """A checkout at the culprit, as ``prepare_repo(ref=culprit)`` leaves it."""
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _investigation(platform="linux1804-64/opt"):
    return Investigation(
        project="autoland",
        hg_revision="hgrev",
        harness="mochitest",
        platform=platform,
        failing_groups=[FailingGroup("dom/base/test/mochitest.ini", ["a.js"])],
        commit_range=CommitRange(head="f" * 40, span=2),
    )


def _fx_ctx(tmp_path):
    return SimpleNamespace(
        source_dir=tmp_path / "src",
        mozconfig=tmp_path / ".mozconfig",
        objdir=tmp_path / "obj",
    )


def _run(
    tmp_path,
    monkeypatch,
    *,
    culprit_bug=1234567,
    outcome=None,
    platform="linux1804-64/opt",
    skip_firefox_build=SKIP_FIREFOX_BUILD,
    actions_recorder=None,
    message="Bug 555 - break it r=me",
    failed=False,
):
    repo = tmp_path / "src"
    culprit = _culprit_repo(repo, message)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "analysis.md").write_text("the sheriff's analysis")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    calls, options = [], []

    async def fake_session(reporter, opts, prompt):
        calls.append(prompt)
        options.append(opts)
        (out_dir / "result.json").write_text(
            json.dumps(outcome or {"proposed_patch": True, "verified": True})
        )
        (out_dir / "summary.md").write_text("your rename missed a selector")
        (out_dir / "fix.md").write_text("## Root cause\n## Patch\n## Verification")
        return _result_msg(is_error=failed)

    async def fake_bootstrap(firefox_dir):
        return {"success": True}

    monkeypatch.setattr(agent, "_run_session", fake_session)
    monkeypatch.setattr(agent, "bootstrap_firefox", fake_bootstrap)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {"type": "sdk"})

    analysis = TestRepairResult(
        classification="regression",
        recommendation="backout",
        culprit_commit=culprit,
        culprit_bug=culprit_bug,
        confidence=0.9,
        num_turns=9,
    )
    result = asyncio.run(
        agent.run_fix(
            bugzilla_mcp_server=None,
            source_repo=repo,
            fx_ctx=_fx_ctx(tmp_path),
            investigation=_investigation(platform),
            analysis=analysis,
            analysis_dir=analysis_dir,
            out_dir=out_dir,
            skip_firefox_build=skip_firefox_build,
            actions_recorder=actions_recorder,
        )
    )
    return result, calls[0], options[0], culprit


def test_the_fix_confirms_the_cause_wherever_it_applies(tmp_path, monkeypatch):
    _, prompt, options, culprit = _run(tmp_path, monkeypatch)
    assert f"Confirm the root cause in the culprit's diff (`git show {culprit}`)" in (
        _flat(prompt)
    )
    assert "every place it applies" in prompt
    assert options.effort == "high"


def test_the_patch_is_a_change_to_the_culprit(tmp_path, monkeypatch):
    _, prompt, _, culprit = _run(tmp_path, monkeypatch)
    assert f"checked out at {culprit} itself" in _flat(prompt)
    assert "squash" in prompt
    assert "not a follow-up" in _flat(prompt)


def test_the_prompt_points_at_the_analysis_runs_documents(tmp_path, monkeypatch):
    _, prompt, _, _ = _run(tmp_path, monkeypatch)
    assert f"The analysis left its work in {tmp_path / 'analysis'}" in _flat(prompt)
    assert "leave it as it is" in _flat(prompt)
    for document in ("summary.md", "analysis.md", "verdict.json", "logs/"):
        assert f"- {document}" in prompt
    assert "may stop there" in _flat(prompt)
    assert str(tmp_path / "src") in prompt


def test_the_prompt_carries_the_failure_context(tmp_path, monkeypatch):
    _, prompt, _, culprit = _run(tmp_path, monkeypatch)
    assert f"blamed commit {culprit} (bug 1234567) (confidence 0.9)" in _flat(prompt)
    assert "- dom/base/test/mochitest.ini (1 failed): a.js" in prompt
    assert "Test harness: mochitest" in prompt
    assert "Failure push: autoland hg revision hgrev" in prompt


def test_the_agent_has_its_own_system_prompt(tmp_path, monkeypatch):
    _, _, options, _ = _run(tmp_path, monkeypatch)
    append = options.system_prompt["append"]
    assert "`git grep`" in append
    assert "git diff --stat" in append
    assert "`treeherder-cli`" in append


def test_the_agent_writes_its_own_documents_for_the_author(tmp_path, monkeypatch):
    _, prompt, _, _ = _run(tmp_path, monkeypatch)
    flat = _flat(prompt)
    assert f"Write summary.md, fix.md and result.json to {tmp_path / 'out'}" in flat
    assert "for the culprit's author" in flat
    for section in ("## Root cause", "## Patch", "## Verification"):
        assert section in prompt
    assert "Do not restate the backout or the CI history" in flat


def test_the_result_is_the_fix_not_the_analysis(tmp_path, monkeypatch):
    result, _, _, culprit = _run(tmp_path, monkeypatch)
    assert result.culprit_commit == culprit
    assert result.culprit_bug == 1234567
    assert result.proposed_patch is True
    assert result.verified is True
    assert result.summary == "your rename missed a selector"
    assert result.fix.startswith("## Root cause")
    assert result.num_turns == 3
    assert not hasattr(result, "recommendation")
    assert (tmp_path / "analysis" / "analysis.md").read_text() == (
        "the sheriff's analysis"
    )


def test_no_patch_is_reported_as_such(tmp_path, monkeypatch):
    result, _, _, _ = _run(tmp_path, monkeypatch, outcome={"proposed_patch": False})
    assert result.proposed_patch is False
    assert result.verified is False


def test_a_failed_session_fails_the_run(tmp_path, monkeypatch):
    with pytest.raises(AgentError, match="fix session failed"):
        _run(tmp_path, monkeypatch, failed=True)


# --- submitting the patch for review ------------------------------------- #


def test_the_revision_is_filed_against_the_culprits_bug(tmp_path, monkeypatch):
    _, prompt, options, _ = _run(
        tmp_path, monkeypatch, actions_recorder=ActionsRecorder()
    )
    assert set(options.allowed_tools) >= ACTION_TOOLS
    assert "actions" in options.mcp_servers
    assert "phabricator_submit_patch" in prompt
    assert "bug_id=1234567" in prompt
    assert '"Bug 1234567 - ' in prompt
    assert "squashed into the culprit" in _flat(prompt)


@pytest.mark.parametrize(
    ("bug", "recorder", "message"),
    [(1234567, None, "Bug 555 - x"), (None, ActionsRecorder(), "no bug here")],
    ids=["no-recorder", "no-bug-anywhere"],
)
def test_reporting_is_skipped_without_a_recorder_and_a_bug(
    tmp_path, monkeypatch, bug, recorder, message
):
    _, prompt, options, _ = _run(
        tmp_path,
        monkeypatch,
        culprit_bug=bug,
        actions_recorder=recorder,
        message=message,
    )
    assert not ACTION_TOOLS & set(options.allowed_tools)
    assert "actions" not in options.mcp_servers
    assert "phabricator_submit_patch" not in prompt


def test_the_revision_stacks_on_the_culprits_own(tmp_path, monkeypatch):
    _, prompt, _, _ = _run(
        tmp_path,
        monkeypatch,
        actions_recorder=ActionsRecorder(),
        message="Bug 555 - break it r=me\n\nDifferential Revision: "
        "https://phabricator.services.mozilla.com/D325120",
    )
    assert "parent_revision_id=325120 (the culprit's own revision, D325120)" in prompt


def test_no_stacking_when_the_culprit_names_no_revision(tmp_path, monkeypatch):
    _, prompt, _, _ = _run(tmp_path, monkeypatch, actions_recorder=ActionsRecorder())
    assert "parent_revision" not in prompt


def test_no_revision_from_a_commit_git_cannot_read(tmp_path):
    assert agent._revision_from_commit(tmp_path, "HEAD") is None


# --- building ------------------------------------------------------------- #


def test_building_is_the_default():
    # Pins config.SKIP_FIREFOX_BUILD, so a change to the default flips this test
    # rather than silently changing every run.
    assert SKIP_FIREFOX_BUILD is False


def test_skipping_the_build_leaves_the_patch_unverified(tmp_path, monkeypatch):
    _, prompt, options, _ = _run(tmp_path, monkeypatch, skip_firefox_build=True)
    assert not (tmp_path / ".mozconfig").exists()
    # The build tool is not merely discouraged, it is not on offer.
    assert BUILD_TOOL not in options.allowed_tools
    assert "firefox" not in options.mcp_servers
    assert "Do not build" in prompt
    assert "unverified" in prompt


def test_the_build_verifies_the_fix(tmp_path, monkeypatch):
    _, prompt, options, _ = _run(tmp_path, monkeypatch)
    assert (tmp_path / ".mozconfig").exists()
    assert BUILD_TOOL in options.allowed_tools
    assert "build_firefox tool" in prompt
    assert "Do not build" not in prompt
    assert "They should pass." in prompt
    assert "virtual display" in prompt
    assert "could not verify" in prompt


def test_non_linux_failure_still_runs_the_test_but_discounts_a_pass(
    tmp_path, monkeypatch
):
    # A failure on Linux is real evidence the patch is wrong; a pass says nothing
    # about the failing platform, so it must not count as verification.
    _, prompt, _, _ = _run(
        tmp_path,
        monkeypatch,
        platform="windows11-64-24h2/opt",
        skip_firefox_build=False,
    )
    assert "mach" in prompt
    assert "proves nothing about windows11-64-24h2/opt" in prompt
    assert "not verified" in prompt


def _mozconfig_for(tmp_path, platform):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fx = _fx_ctx(tmp_path)
    agent._write_mozconfig(fx, _investigation(platform))
    return fx.mozconfig.read_text()


def test_mozconfig_mirrors_the_ci_build_variant(tmp_path):
    opt = _mozconfig_for(tmp_path / "opt", "linux1804-64-qr/opt")
    assert "--disable-debug" in opt and "--enable-optimize" in opt
    assert "sanitizer" not in opt

    dbg = _mozconfig_for(tmp_path / "dbg", "linux1804-64-qr/debug")
    assert "--enable-debug" in dbg

    # A plain build cannot trigger a sanitizer or coverage failure at all.
    asan = _mozconfig_for(tmp_path / "asan", "linux1804-64-asan-qr/opt")
    assert "--enable-address-sanitizer" in asan and "--disable-jemalloc" in asan

    tsan = _mozconfig_for(tmp_path / "tsan", "linux1804-64-tsan-qr/opt")
    assert "--enable-thread-sanitizer" in tsan

    ccov = _mozconfig_for(tmp_path / "ccov", "linux1804-64-ccov/opt")
    assert "--enable-coverage" in ccov


def test_mozconfig_overwrites_a_foreign_one(tmp_path):
    # /workspace is shared with the other agents, so a leftover mozconfig points
    # the build at a foreign objdir with foreign flags.
    fx = _fx_ctx(tmp_path)
    fx.mozconfig.write_text(
        "ac_add_options --enable-release\n"
        "mk_add_options MOZ_OBJDIR=/workspace/firefox/objdir-build-repair\n"
    )
    agent._write_mozconfig(fx, _investigation())
    written = fx.mozconfig.read_text()
    assert "objdir-build-repair" not in written
    assert "--enable-release" not in written
    assert str(fx.objdir) in written
