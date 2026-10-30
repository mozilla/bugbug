import pytest
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)
from hackbot_agents.test_repair_fix.agent import TestRepairFixResult
from hackbot_agents.test_repair_fix.notify import build_email, recipients

HG_REVISION = "341517e50536aabbccddeeff00112233445566"
GIT_REVISION = "7b15e34863cf6b30b613ffadf9d6431fe5a55585"
TASK_ID = "JfAGrrtoQPS3fXrwZmq1Pg"
GIT_URL = f"https://github.com/mozilla-firefox/firefox/commit/{GIT_REVISION}"


def _investigation():
    return Investigation(
        project="autoland",
        hg_revision=HG_REVISION,
        harness="xpcshell",
        platform="linux1804-64/opt",
        failing_groups=[
            FailingGroup("toolkit/modules/tests/xpcshell/xpcshell.toml", ["a.js"])
        ],
        commit_range=CommitRange(head="f" * 40, span=4),
        label="test-linux1804-64/opt-xpcshell-1",
    )


def _result(**overrides):
    fields = {
        "culprit_commit": GIT_REVISION,
        "culprit_bug": 2061487,
        "proposed_patch": True,
        "verified": True,
        "summary": "Your rename missed one selector.",
        "fix": "## Root cause\n\nthe rename\n\n## Patch\n\none line",
        "num_turns": 6,
    }
    return TestRepairFixResult(**{**fields, **overrides})


def _email(result=None, **kwargs):
    return build_email(
        result or _result(),
        _investigation(),
        task_id=TASK_ID,
        run_id="fix-run",
        analysis_run_id="analysis-run",
        **kwargs,
    )


def test_the_subject_names_the_patch_and_whether_it_was_verified():
    subject, _ = _email()
    assert subject == (
        "[test-repair-fix] Patch for toolkit/modules/tests/xpcshell/xpcshell.toml"
        f" regressed by {GIT_REVISION[:12]}, verified (autoland)"
    )
    subject, _ = _email(_result(verified=False))
    assert ", unverified (autoland)" in subject


def test_the_body_is_the_fix_for_the_developer():
    _, body = _email(culprit_author="author@mozilla.com")
    assert body.startswith("# Proposed fix")
    assert "## Fix" in body
    assert "### Root cause" in body
    assert "- **Analysis:** https://hackbot.moz.tools/runs/analysis-run" in body
    assert "- **Hackbot Run:** https://hackbot.moz.tools/runs/fix-run" in body
    # The backout decision is the analysis' to report.
    assert "Verdict" not in body
    assert "confidence" not in body


def test_the_summary_stands_in_for_a_missing_fix_document():
    _, body = _email(_result(fix=""))
    assert "## Fix" not in body
    assert "Your rename missed one selector." in body


def test_the_author_is_addressed_when_there_is_a_patch_for_them():
    assert recipients(_result(), "author@mozilla.com") == ["author@mozilla.com"]


def test_no_patch_reaches_nobody_individually():
    assert recipients(_result(proposed_patch=False), "author@mozilla.com") == []
    assert recipients(_result(), None) == []


@pytest.mark.parametrize(
    "classification", ["intermittent", "autoclassified intermittent"]
)
def test_the_author_is_not_addressed_while_classified_intermittent(classification):
    assert recipients(_result(), "author@mozilla.com", classification) == []


def test_the_author_is_told_up_front_why_they_are_on_the_email():
    _, body = _email(culprit_author="author@mozilla.com")
    why = f"**author@mozilla.com**, your [`{GIT_REVISION[:12]}`]({GIT_URL}) was backed"
    assert why in body
    assert body.index(why) < body.index("- **Failing:**")
    assert "not to land on its own" in body


def test_no_author_note_when_nobody_is_addressed():
    _, body = _email(culprit_author="author@mozilla.com", classification="intermittent")
    assert "author@mozilla.com**," not in body


def test_the_patch_section_frames_a_placeholder_the_apply_step_fills():
    _, body = _email()
    assert body.endswith("## Proposed patch\n\n```diff\n{patch}\n```")
    _, body = _email(_result(proposed_patch=False))
    assert "{patch}" not in body


def test_a_stacked_revision_comes_with_the_reland_recipe():
    _, body = _email(revision_pending=True, parent_revision=325120)
    assert (
        "**Phabricator:** *Apply pending actions* on the "
        "[run page](https://hackbot.moz.tools/runs/fix-run) files this patch as"
        " a child revision of D325120. To reland:" in body
    )
    recipe = body.split("```\n", 2)[1]
    assert recipe.splitlines() == [
        "moz-phab patch D325120",
        "moz-phab patch D<new> --apply-to @",
        "git rebase -i @~1   # squash the fix into your patch",
        "moz-phab patch D<next> --skip-dependencies   # each later patch in your stack",
        "moz-phab submit",
    ]
    assert body.index("To reland:") < body.index("## Proposed patch")


def test_an_unstacked_revision_says_how_to_pull_it_onto_the_patch():
    _, body = _email(revision_pending=True)
    assert (
        "files this patch as a WIP revision on "
        "[bug 2061487](https://bugzilla.mozilla.org/show_bug.cgi?id=2061487)." in body
    )
    assert "To reland:" not in body


def test_no_submit_step_without_a_pending_revision():
    _, body = _email()
    assert "Phabricator" not in body
