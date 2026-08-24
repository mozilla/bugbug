from hackbot_agents.test_repair.agent import TestRepairResult
from hackbot_agents.test_repair.notify import (
    build_email,
    build_message,
    sheriff_action_required,
)
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)

HG_REVISION = "341517e50536aabbccddeeff00112233445566"
GIT_REVISION = "7b15e34863cf6b30b613ffadf9d6431fe5a55585"
TASK_ID = "JfAGrrtoQPS3fXrwZmq1Pg"

GIT_URL = f"https://github.com/mozilla-firefox/firefox/commit/{GIT_REVISION}"
HG_URL = f"https://hg.mozilla.org/mozilla-unified/rev/{HG_REVISION}"


def _investigation(groups=None, label="test-linux1804-64/opt-xpcshell-1"):
    return Investigation(
        project="autoland",
        hg_revision=HG_REVISION,
        harness="xpcshell",
        platform="linux1804-64/opt",
        failing_groups=groups
        if groups is not None
        else [FailingGroup("toolkit/modules/tests/xpcshell/xpcshell.toml", ["a.js"])],
        commit_range=CommitRange(head=GIT_REVISION, span=4),
        label=label,
    )


def _result(**overrides):
    fields = {
        "classification": "regression",
        "recommendation": "backout",
        "culprit_commit": GIT_REVISION,
        "culprit_bug": 2061487,
        "confidence": 0.7,
        "summary": "Verdict\n\ntest_Region.js fails 20/20 times in chaos mode.",
        "num_turns": 12,
    }
    return TestRepairResult(**{**fields, **overrides})


def _message(result=None, investigation=None, **kwargs):
    return build_message(
        result or _result(),
        investigation or _investigation(),
        task_id=TASK_ID,
        run_id="1218e630-78c8",
        **kwargs,
    )


def test_a_known_intermittent_is_not_worth_a_notification():
    assert not sheriff_action_required(
        _result(
            classification="intermittent",
            recommendation="do_not_backout",
            culprit_commit=None,
        )
    )


def test_an_unconfirmed_intermittent_still_asks_for_a_retrigger():
    assert sheriff_action_required(
        _result(
            classification="intermittent",
            recommendation="rerun",
            culprit_commit=None,
        )
    )


def test_every_regression_verdict_is_notified():
    assert sheriff_action_required(_result())
    assert sheriff_action_required(_result(recommendation="rerun"))
    # No culprit survived, so there is nothing to back out -- but a regression the
    # agent could not pin down is still a sheriff's problem.
    assert sheriff_action_required(
        _result(recommendation="do_not_backout", culprit_commit=None)
    )


def test_reports_the_verdict_and_its_context_in_five_lines():
    assert _message(culprit_author="standard8@mozilla.com").splitlines() == [
        "*test-repair: BACK OUT the culprit* (regression, confidence 0.7)",
        "Failing: `toolkit/modules/tests/xpcshell/xpcshell.toml` in "
        "`test-linux1804-64/opt-xpcshell-1`",
        "Jobs: <https://treeherder.mozilla.org/#/jobs"
        f"?repo=autoland&revision={HG_REVISION}&selectedTaskRun={TASK_ID}|Treeherder>, "
        f"<https://firefox-ci-tc.services.mozilla.com/tasks/{TASK_ID}"
        f"|Taskcluster {TASK_ID}>",
        f"Push: autoland <{HG_URL}|hg 341517e50536> / <{GIT_URL}|github 7b15e34863cf>",
        f"Culprit: <{GIT_URL}|github 7b15e34863cf> by standard8@mozilla.com "
        "(<https://bugzilla.mozilla.org/show_bug.cgi?id=2061487|bug 2061487>)",
        "<https://hackbot.moz.tools/runs/1218e630-78c8|Hackbot run details>",
        "",
        "Verdict",
        "",
        "test_Region.js fails 20/20 times in chaos mode.",
    ]


def test_reports_the_verdict_in_full():
    verdict = "A long verdict. " * 60
    assert verdict.strip() in _message(_result(summary=verdict))


def test_omits_the_author_when_it_could_not_be_resolved():
    assert " by " not in _message()


def test_lists_candidates_when_no_single_commit_was_blamed():
    result = _result(
        recommendation="rerun",
        culprit_commit=None,
        culprit_bug=None,
        candidate_commits=["1111111111111111", "2222222222222222"],
    )
    message = _message(result)
    assert message.startswith("*test-repair: RETRIGGER the job* (regression,")
    assert "Culprit: not narrowed down, candidates <" in message
    assert "|github 111111111111>, <" in message


def test_says_so_when_nothing_was_blamed():
    result = _result(culprit_commit=None, culprit_bug=None)
    assert "Culprit: none identified" in _message(result)


def test_names_a_known_intermittent():
    result = _result(
        classification="intermittent",
        recommendation="do_not_backout",
        culprit_commit=None,
        culprit_bug=None,
        intermittent_bug=1234,
    )
    message = _message(result)
    assert message.startswith(
        "*test-repair: DO NOT back out (intermittent)* (intermittent,"
    )
    assert (
        "Culprit: none identified "
        "(<https://bugzilla.mozilla.org/show_bug.cgi?id=1234|bug 1234>)" in message
    )


def test_a_patch_is_advice_for_the_author_not_an_alternative_action():
    message = _message(_result(proposed_patch=True))
    assert "*test-repair: BACK OUT the culprit*" in message.splitlines()[0]
    assert "squash it into the existing patches and reland" in message
    assert "rather than landing it as a follow-up" in message
    assert "The backout still stands." in message


def test_the_patch_line_comes_before_the_run_link():
    lines = _message(_result(proposed_patch=True)).splitlines()
    patch = next(i for i, line in enumerate(lines) if "Patch attached" in line)
    run = next(i for i, line in enumerate(lines) if "Hackbot run details" in line)
    assert patch < run


def test_no_patch_line_without_a_patch():
    assert "Patch attached" not in _message()


def test_lists_every_failing_group():
    groups = [FailingGroup(f"g{i}.ini", ["a.js"]) for i in range(4)]
    message = _message(investigation=_investigation(groups=groups))
    assert "Failing: `g0.ini`, `g1.ini`, `g2.ini`, `g3.ini` in " in message


def test_falls_back_when_groups_and_label_are_unknown():
    message = _message(investigation=_investigation(groups=[], label=""))
    assert "Failing: tests not resolved in `xpcshell on linux1804-64/opt`" in message


def _email(result=None, investigation=None, **kwargs):
    return build_email(
        result or _result(),
        investigation or _investigation(),
        task_id=TASK_ID,
        run_id="1218e630-78c8",
        **kwargs,
    )


def test_the_email_subject_names_the_verdict_and_the_failing_group():
    subject, _ = _email()
    assert subject == (
        "[test-repair] BACK OUT the culprit - "
        "toolkit/modules/tests/xpcshell/xpcshell.toml (autoland)"
    )


def test_extra_failing_groups_are_counted_in_the_subject():
    subject, _ = _email(
        investigation=_investigation(
            groups=[FailingGroup("a.toml", ["a.js"]), FailingGroup("b.toml", ["b.js"])]
        )
    )
    assert subject.startswith("[test-repair] BACK OUT the culprit - a.toml (+1 more)")


def test_an_already_actioned_failure_is_flagged_in_subject_and_body():
    subject, body = _email(already_actioned="fixed by commit")
    assert subject.startswith("[test-repair] [already actioned] ")
    assert "Already actioned by a sheriff" in body
    assert "_fixed by commit_" in body


def test_the_email_links_every_identifier():
    _, body = _email()
    assert f"[`{GIT_REVISION[:12]}`]({GIT_URL})" in body
    assert f"[`{HG_REVISION[:12]}`]({HG_URL})" in body
    assert (
        f"[`{TASK_ID}`](https://firefox-ci-tc.services.mozilla.com/tasks/{TASK_ID})"
        in body
    )
    assert "https://hackbot.moz.tools/runs/1218e630-78c8" in body


def test_the_culprit_author_is_named_when_known():
    _, body = _email(culprit_author="author@mozilla.com")
    assert "by author@mozilla.com" in body


def test_agent_prose_nests_under_the_email_headings():
    _, body = _email(result=_result(analysis="# Root cause\n\ndetail"))
    assert "## Analysis" in body
    assert "### Root cause" in body


def test_the_patch_is_quoted_when_the_run_produced_one():
    _, body = _email(patch="--- a\n+++ b\n+fix")
    assert "## Proposed patch" in body
    assert "```diff\n--- a\n+++ b\n+fix\n```" in body


def test_no_patch_section_without_a_patch():
    _, body = _email()
    assert "## Proposed patch" not in body


def test_an_intermittent_verdict_is_still_emailed():
    # Unlike Slack, the email is not filtered by sheriff_action_required.
    subject, body = _email(
        result=_result(
            classification="intermittent",
            recommendation="do_not_backout",
            culprit_commit=None,
        )
    )
    assert "DO NOT back out (intermittent)" in subject
    assert "**Classification:** intermittent" in body
