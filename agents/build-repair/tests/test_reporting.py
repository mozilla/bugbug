"""Tests for how the fix stage submits its fix for review.

Both sessions are faked, so these check the wiring the agent hands the SDK (which
action tools each stage may call, and what the fix prompt asks for) rather than
what a model does with it.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from hackbot_agents.build_repair import agent, resolve
from hackbot_runtime import ActionsRecorder

ACTION_TOOLS = {"mcp__actions__phabricator_submit_patch"}


def _result_msg():
    return SimpleNamespace(
        is_error=False, total_cost_usd=0.1, num_turns=3, result=None, subtype=None
    )


FAILURE_COMMIT = "a" * 40


def _run(tmp_path, monkeypatch, *, bug_id, actions_recorder, push_bug=None, blame=None):
    """Run the agent with both sessions faked, returning their (options, prompt).

    ``push_bug`` is the bug the pushlog reported for the failure commit, which is
    what the agent falls back to — ``bug_id`` is never set in practice. ``blame``
    is what stage 1 writes to blame.json; the failure commit when omitted.
    """
    sessions = []

    async def fake_session(reporter, options, prompt, captured, tracked):
        sessions.append((options, prompt))
        # Stand in for the log treeherder-cli fetches, so the real _check_blocked
        # guard between the stages is exercised rather than bypassed.
        out = Path(options.add_dirs[-1]) / "out"
        logs = out / "logs" / "job_1"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "live_backing_log.log").write_text("ERROR - boom\n")
        if blame is not None:
            (out / "blame.json").write_text(json.dumps({"blamed_commit": blame}))
        return _result_msg()

    monkeypatch.setattr(agent, "_run_session", fake_session)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {"type": "sdk"})

    result = asyncio.run(
        agent.run_build_repair(
            bugzilla_mcp_server={"type": "http", "url": "http://broker/mcp"},
            source_repo=tmp_path,
            fx_ctx=SimpleNamespace(
                mozconfig=tmp_path / ".mozconfig", objdir=tmp_path / "objdir"
            ),
            bug_id=bug_id,
            commit_bugs={FAILURE_COMMIT: push_bug} if push_bug else {},
            git_commits=[FAILURE_COMMIT],
            project="autoland",
            hg_revision="abc123",
            failure_tasks={"build-linux": "taskid"},
            actions_recorder=actions_recorder,
        )
    )
    assert len(sessions) == 2
    return result, sessions[0], sessions[1]


def test_actions_are_wired_into_the_fix_stage_only(tmp_path, monkeypatch):
    _, (analysis_opts, _), (fix_opts, fix_prompt) = _run(
        tmp_path, monkeypatch, bug_id=1234567, actions_recorder=ActionsRecorder()
    )

    assert not ACTION_TOOLS & set(analysis_opts.allowed_tools)
    assert "actions" not in analysis_opts.mcp_servers

    assert ACTION_TOOLS <= set(fix_opts.allowed_tools)
    assert "actions" in fix_opts.mcp_servers
    assert "bugzilla" in fix_opts.mcp_servers


def test_fix_prompt_asks_for_a_revision_and_nothing_else(tmp_path, monkeypatch):
    _, _, (_, fix_prompt) = _run(
        tmp_path, monkeypatch, bug_id=1234567, actions_recorder=ActionsRecorder()
    )

    assert "phabricator_submit_patch" in fix_prompt
    assert "bug_id=1234567" in fix_prompt
    # The developer reviews the patch in the UI; the agent never touches the bug.
    assert "bugzilla_add_comment" not in fix_prompt


def test_the_pushs_bug_stands_in_for_an_unset_bug_id(tmp_path, monkeypatch):
    """A run started from nothing but a failing task still files the revision."""
    result, (_, analysis_prompt), (fix_opts, fix_prompt) = _run(
        tmp_path,
        monkeypatch,
        bug_id=None,
        actions_recorder=ActionsRecorder(),
        push_bug=2063979,
    )

    assert ACTION_TOOLS <= set(fix_opts.allowed_tools)
    assert "bug_id=2063979" in fix_prompt
    assert '"Bug 2063979 - ' in fix_prompt
    assert result.bug_id == 2063979
    # Stage 1 gets it too, so the analysis can read the bug from Bugzilla.
    assert "2063979" in analysis_prompt


def test_an_explicit_bug_id_wins_over_the_push(tmp_path, monkeypatch):
    result, _, (_, fix_prompt) = _run(
        tmp_path,
        monkeypatch,
        bug_id=1234567,
        actions_recorder=ActionsRecorder(),
        push_bug=2063979,
    )

    assert "bug_id=1234567" in fix_prompt
    assert result.bug_id == 1234567


def test_fix_prompt_asks_for_a_bug_prefixed_commit(tmp_path, monkeypatch):
    _, _, (_, fix_prompt) = _run(
        tmp_path, monkeypatch, bug_id=1234567, actions_recorder=ActionsRecorder()
    )

    assert "git add" in fix_prompt
    assert '"Bug 1234567 - ' in fix_prompt


@pytest.mark.parametrize(
    ("bug_id", "recorder"),
    [(1234567, None), (None, ActionsRecorder())],
    ids=["no-recorder", "no-bug-anywhere"],
)
def test_reporting_is_skipped_without_a_recorder_and_a_bug(
    tmp_path, monkeypatch, bug_id, recorder
):
    _, (analysis_opts, _), (fix_opts, fix_prompt) = _run(
        tmp_path, monkeypatch, bug_id=bug_id, actions_recorder=recorder
    )

    for opts in (analysis_opts, fix_opts):
        assert not ACTION_TOOLS & set(opts.allowed_tools)
        assert "actions" not in opts.mcp_servers
    assert "phabricator_submit_patch" not in fix_prompt


def test_a_run_with_no_bug_anywhere_still_commits(tmp_path, monkeypatch):
    """No bug means no revision, but the patch still needs a message."""
    _, _, (_, fix_prompt) = _run(
        tmp_path, monkeypatch, bug_id=None, actions_recorder=ActionsRecorder()
    )

    assert '"No bug - ' in fix_prompt
    assert "phabricator_submit_patch" not in fix_prompt


# --- pushlog bug resolution --------------------------------------------- #


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ("Bug 2063979 - [Linux] Disable HW video decoding r=stransky", 2063979),
        ("Bug 111 - a fix\n\nDifferential Revision: https://phab/D1", 111),
        ("bug 222 - lowercase still counts", 222),
        ("No bug - tidy up a comment", None),
        ("Backed out changeset abc123 for build bustage", None),
        ("", None),
    ],
)
def test_bug_is_read_off_the_pushlog_description(desc, expected):
    assert resolve._bug_from_desc(desc) == expected


def test_push_commits_pair_each_commit_with_its_bug(monkeypatch):
    """The pushlog request already returns descriptions, so no extra lookup."""
    monkeypatch.setattr(
        resolve,
        "_get_json",
        lambda url: {
            "pushes": {
                "1": {
                    "git_changesets": ["g1", "g2"],
                    "changesets": [
                        {"node": "h1", "desc": "Bug 111 - first"},
                        {"node": "h2", "desc": "No bug - second"},
                    ],
                }
            }
        },
    )

    assert resolve._push_git_commits("autoland", "h2") == (
        ["g1", "g2"],
        {"g1": 111},
    )


def test_an_earlier_pushs_culprit_gets_its_bug_from_the_checkout(tmp_path, monkeypatch):
    """The pushlog mapping misses an out-of-push culprit; the checkout has it."""
    earlier = "b" * 40
    monkeypatch.setattr(
        agent, "_bug_from_commit", lambda repo, sha: 555 if sha == earlier else None
    )
    result, _, (fix_opts, fix_prompt) = _run(
        tmp_path,
        monkeypatch,
        bug_id=None,
        actions_recorder=ActionsRecorder(),
        push_bug=2063979,
        blame=earlier,
    )

    assert ACTION_TOOLS <= set(fix_opts.allowed_tools)
    assert "bug_id=555" in fix_prompt
    assert result.bug_id == 555
    assert result.blamed_commit == earlier


def test_bug_is_read_off_the_local_commit_subject(tmp_path):
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "Bug 555 - an earlier fix r=me",
        ],
        check=True,
        env=env,
    )

    assert agent._bug_from_commit(tmp_path, "HEAD") == 555


def test_no_bug_from_a_commit_git_cannot_read(tmp_path):
    assert agent._bug_from_commit(tmp_path, "HEAD") is None
