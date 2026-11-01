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


def test_the_email_subject_names_the_failing_group_and_the_culprit():
    subject, _ = _email()
    assert subject == (
        "[test-repair] toolkit/modules/tests/xpcshell/xpcshell.toml regressed by "
        f"{GIT_REVISION[:12]} (autoland)"
    )


def test_extra_failing_groups_are_counted_in_the_subject():
    subject, _ = _email(
        investigation=_investigation(
            groups=[FailingGroup("a.toml", ["a.js"]), FailingGroup("b.toml", ["b.js"])]
        )
    )
    assert subject.startswith("[test-repair] a.toml (+1 more) regressed by")


def test_the_subject_says_when_no_culprit_was_narrowed_down():
    subject, _ = _email(_result(culprit_commit=None, recommendation="do_not_backout"))
    assert (
        "Regression in toolkit/modules/tests/xpcshell/xpcshell.toml, culprit not"
        in subject
    )
    subject, _ = _email(_result(culprit_commit=None, recommendation="rerun"))
    assert "Unclear failure in " in subject
    assert "retrigger suggested" in subject


def test_the_context_bullets():
    _, body = _email(culprit_author="author@mozilla.com")
    bullets = [line for line in body.splitlines() if line.startswith("- **")]
    assert bullets == [
        "- **Failing:** `toolkit/modules/tests/xpcshell/xpcshell.toml` in "
        "`test-linux1804-64/opt-xpcshell-1`",
        "- **Jobs:** [Treeherder](https://treeherder.mozilla.org/#/jobs?repo=autoland"
        f"&revision={HG_REVISION}&selectedTaskRun={TASK_ID}), "
        f"[Taskcluster](https://firefox-ci-tc.services.mozilla.com/tasks/{TASK_ID})",
        f"- **Push:** autoland [hg {HG_REVISION[:12]}]({HG_URL}) / "
        f"[git {GIT_REVISION[:12]}]({GIT_URL})",
        f"- **Culprit:** [`{GIT_REVISION[:12]}`]({GIT_URL}) by author@mozilla.com, "
        "[bug 2061487](https://bugzilla.mozilla.org/show_bug.cgi?id=2061487)",
        "- **Verdict:** regression, confidence 0.7; back out the culprit",
        "- **Current Treeherder classification:** not classified",
        "- **Hackbot Run:** https://hackbot.moz.tools/runs/1218e630-78c8",
    ]


def test_the_recommendation_is_stated_without_shouting():
    _, body = _email()
    assert "back out the culprit" in body
    assert "BACK OUT" not in body


def test_candidates_are_listed_when_no_single_commit_was_blamed():
    _, body = _email(
        _result(
            culprit_commit=None,
            culprit_bug=None,
            candidate_commits=["1" * 40, "2" * 40],
        )
    )
    assert "- **Culprit:** not narrowed down, candidates [`111111111111`]" in body


def test_the_intermittent_bug_is_named_when_nothing_was_blamed():
    _, body = _email(
        _result(culprit_commit=None, culprit_bug=None, intermittent_bug=1234)
    )
    assert (
        "- **Culprit:** none identified, "
        "[bug 1234](https://bugzilla.mozilla.org/show_bug.cgi?id=1234)" in body
    )


def test_agent_prose_nests_under_the_analysis_heading():
    _, body = _email(result=_result(analysis="# Root cause\n\ndetail"))
    assert "## Analysis" in body
    assert "### Root cause" in body


def test_the_summary_is_not_repeated_when_there_is_an_analysis():
    _, body = _email(result=_result(analysis="# Root cause\n\ndetail"))
    assert "Summary" not in body
    assert "test_Region.js fails 20/20 times" not in body


def test_the_summary_stands_in_for_a_missing_analysis():
    _, body = _email()
    assert "## Analysis" not in body
    assert "test_Region.js fails 20/20 times in chaos mode." in body


def test_an_intermittent_verdict_is_still_emailed():
    # Unlike Slack, the email is not filtered by sheriff_action_required.
    subject, body = _email(
        result=_result(
            classification="intermittent",
            recommendation="do_not_backout",
            culprit_commit=None,
        )
    )
    assert subject.startswith(
        "[test-repair] Intermittent failure in toolkit/modules/tests/xpcshell/"
    )
    assert "- **Verdict:** intermittent, confidence 0.7; no backout" in body


def test_the_verdict_email_carries_no_patch_and_addresses_no_author():
    _, body = _email(
        culprit_author="author@mozilla.com", classification="fixed by commit"
    )
    assert body.startswith("# Test failure analysis")
    assert "- **Current Treeherder classification:** fixed by commit" in body
    assert "author@mozilla.com**," not in body
    assert "Proposed patch" not in body
    assert "{patch}" not in body
