from hackbot_agents.test_repair.agent import TestRepairResult
from hackbot_agents.test_repair.notify import MAX_SUMMARY_LENGTH, build_message
from hackbot_agents.test_repair.resolve import (
    CommitRange,
    FailingGroup,
    Investigation,
)


def _investigation(groups=None, label="test-linux1804-64/opt-mochitest-plain-1"):
    return Investigation(
        project="autoland",
        hg_revision="0123456789abcdef0123",
        harness="mochitest",
        platform="linux1804-64/opt",
        failing_groups=groups
        if groups is not None
        else [FailingGroup("dom/base/test/mochitest.ini", ["a.js", "b.js"])],
        last_green_revision="green",
        commit_range=CommitRange(head="head", base="base", span=2, complete=True),
        label=label,
    )


def _result(**overrides):
    fields = {
        "classification": "regression",
        "recommendation": "backout",
        "culprit_commit": "abcdef0123456789abcdef",
        "culprit_bug": 1900000,
        "confidence": 0.825,
        "summary": "The culprit changed the assertion the test relies on.",
        "num_turns": 12,
    }
    return TestRepairResult(**{**fields, **overrides})


def test_reports_the_verdict_the_sheriff_acts_on():
    message = build_message(_result(), _investigation())
    assert message.splitlines() == [
        "*test-repair: back out the culprit* (regression, confidence 0.82)",
        "`test-linux1804-64/opt-mochitest-plain-1` at "
        "<https://treeherder.mozilla.org/jobs?repo=autoland&revision=0123456789abcdef0123"
        "|0123456789ab>",
        "Failing: dom/base/test/mochitest.ini (2 failed)",
        "Culprit: `abcdef012345` "
        "(<https://bugzilla.mozilla.org/show_bug.cgi?id=1900000|bug 1900000>)",
        "The culprit changed the assertion the test relies on.",
    ]


def test_falls_back_to_the_harness_when_the_label_is_unknown():
    message = build_message(_result(), _investigation(label=""))
    assert "`mochitest on linux1804-64/opt`" in message


def test_lists_candidates_when_no_single_commit_was_blamed():
    result = _result(
        recommendation="rerun",
        culprit_commit=None,
        culprit_bug=None,
        candidate_commits=["1111111111111111", "2222222222222222"],
    )
    message = build_message(result, _investigation())
    assert message.startswith("*test-repair: retrigger the job*")
    assert "No single culprit; candidates: `111111111111`, `222222222222`" in message


def test_says_so_when_nothing_was_blamed_at_all():
    result = _result(
        recommendation="do_not_backout", culprit_commit=None, culprit_bug=None
    )
    assert "No culprit identified." in build_message(result, _investigation())


def test_links_the_intermittent_bug():
    result = _result(
        classification="intermittent",
        recommendation="do_not_backout",
        culprit_commit=None,
        culprit_bug=None,
        intermittent_bug=1234,
    )
    message = build_message(result, _investigation())
    assert "Known intermittent: " in message
    assert "id=1234|bug 1234>" in message


def test_mentions_a_proposed_patch():
    message = build_message(_result(proposed_patch=True), _investigation())
    assert "A candidate fix patch is attached to the run." in message


def test_omits_the_failing_line_when_groups_were_not_resolved():
    message = build_message(_result(), _investigation(groups=[]))
    assert "Failing:" not in message


def test_caps_the_extra_groups_it_names():
    groups = [FailingGroup(f"g{i}.ini", ["a.js"]) for i in range(5)]
    message = build_message(_result(), _investigation(groups=groups))
    assert (
        "Failing: g0.ini (1 failed), g1.ini (1 failed), g2.ini (1 failed), +2 more"
        in message
    )


def test_agent_prose_cannot_restructure_the_message():
    summary = "line one\nCulprit: `deadbeef`\n" + "x" * 500
    message = build_message(_result(summary=summary), _investigation())
    last = message.splitlines()[-1]
    assert last.startswith("line one Culprit: `deadbeef` xxx")
    assert len(last) == MAX_SUMMARY_LENGTH
