"""Tests for the skips that stop a run before it costs anything."""

import pytest
from conftest import LATER, NOW, quiet, with_needinfo
from hackbot_agents.gecko_triage import preflight
from hackbot_agents.gecko_triage.config import (
    PEOPLE,
    TERMS,
)
from hackbot_agents.gecko_triage.preflight import (
    BugUnreadable,
    reporter_audience,
    require_state,
    skip_reason,
)
from hackbot_agents.gecko_triage.state import TriageState

# The employee checks need the configured domain; every other address is an example one.
EMPLOYEE = f"dev{PEOPLE.employee_domain}"


def _state(bug: dict, comments: list[dict]) -> TriageState:
    return TriageState(bug=bug, comments=comments, now=NOW)


def test_an_ordinary_new_bug_is_triaged(new_state):
    assert skip_reason(new_state) is None


def test_out_of_scope_component(bug_new, comments_new):
    for product, component in (
        ("Core", "Graphics"),
        ("Firefox", "Audio/Video: Playback"),
        ("Core", "Audio/Video: playback"),
    ):
        bug = dict(bug_new, product=product, component=component)
        assert "out of scope" in skip_reason(_state(bug, comments_new))


def test_meta_bug(bug_new, comments_new):
    assert "meta" in skip_reason(_state(dict(bug_new, keywords=["meta"]), comments_new))
    for summary in (
        "[Meta] playback issues",
        "Meta bug for cubeb",
        "the meta-bug for AV1",
    ):
        assert "meta" in skip_reason(
            _state(dict(bug_new, summary=summary), comments_new)
        )
    # "metadata" is not a meta bug.
    assert (
        skip_reason(_state(dict(bug_new, summary="metadata parse error"), comments_new))
        is None
    )


def test_resolved(bug_new, comments_new):
    bug = dict(bug_new, status="RESOLVED", resolution="FIXED")
    assert skip_reason(_state(bug, comments_new)) == "already RESOLVED FIXED"
    bug = dict(bug_new, status="VERIFIED", resolution="WORKSFORME")
    assert "VERIFIED" in skip_reason(_state(bug, comments_new))


def test_restricted(bug_new, comments_new):
    bug = dict(bug_new, groups=["core-security"])
    assert "restricted" in skip_reason(_state(bug, comments_new))


def test_a_bug_the_broker_withholds_is_skipped_as_restricted():
    state = TriageState(bug={"id": 9000001}, now=NOW, restricted=True)
    assert require_state(state) is state
    assert skip_reason(state) == "restricted bug (withheld by the broker)"


def test_a_public_security_bug_is_skipped(bug_new, comments_new):
    for keywords in (["sec-high"], ["csectype-uaf"], ["security"], ["perf", "sec-low"]):
        bug = dict(bug_new, keywords=keywords)
        assert skip_reason(_state(bug, comments_new)) == "security bug", keywords
    for keywords in (["secure-context"], ["perf"], []):
        bug = dict(bug_new, keywords=keywords)
        assert skip_reason(_state(bug, comments_new)) is None, keywords


def test_opted_out_whiteboard(bug_new, comments_new):
    bug = dict(bug_new, whiteboard=f"[foo]{TERMS.whiteboard.skip}")
    assert TERMS.whiteboard.skip in skip_reason(_state(bug, comments_new))


def test_an_untriaged_bug_filed_by_a_non_qa_employee_is_triaged(bug_new, comments_new):
    bug = dict(bug_new, creator=EMPLOYEE, severity="--")
    bug["creator_detail"] = dict(bug_new["creator_detail"], real_name="Dev Eloper")
    assert skip_reason(_state(bug, comments_new)) is None


def test_a_triaged_bug_filed_by_a_non_qa_employee_is_skipped(bug_new, comments_new):
    bug = dict(bug_new, creator=EMPLOYEE, severity="S3")
    bug["creator_detail"] = dict(bug_new["creator_detail"], real_name="Dev Eloper")
    assert "not QA" in skip_reason(_state(bug, comments_new))

    # QA signals: the QA whiteboard, a marker in the real name, or a listed account.
    qa = dict(bug, cf_qa_whiteboard="[qa-triaged]")
    assert skip_reason(_state(qa, comments_new)) is None
    qa = dict(bug)
    qa["creator_detail"] = dict(bug["creator_detail"], real_name="Dev Eloper, QA")
    assert skip_reason(_state(qa, comments_new)) is None

    # A non-employee with a QA-looking name is just a reporter.
    outside = dict(bug_new, creator="qa@example.com", severity="S3")
    assert skip_reason(_state(outside, comments_new)) is None


def test_a_listed_qa_account_is_qa(bug_new, monkeypatch):
    people = PEOPLE.model_copy(update={"qa_accounts": {"tester@example.com"}})
    monkeypatch.setattr(preflight, "PEOPLE", people)
    bug = dict(bug_new, creator="Tester@example.com")
    bug["creator_detail"] = dict(bug_new["creator_detail"], real_name="Some Tester")
    assert preflight.is_qa(bug)


def test_reporter_audience(bug_new):
    dev = dict(bug_new, creator=EMPLOYEE)
    dev["creator_detail"] = dict(bug_new["creator_detail"], real_name="Dev Eloper")
    assert reporter_audience(dev) == "developer"
    assert reporter_audience(dict(dev, cf_qa_whiteboard="[qa]")) == "qa"
    assert reporter_audience(dict(bug_new, creator="user@example.com")) == "reporter"


SKIP_BOT_LAST = (
    "gecko-triage or a bot alias wrote the newest comment and nothing that changes "
    "the analysis happened after it"
)


def test_a_bug_where_the_bot_has_the_last_word_is_skipped(bug_new, comments_returning):
    # The bot's comment and its field changes land in one transaction.
    assert skip_reason(_state(quiet(bug_new), comments_returning[:3])) == SKIP_BOT_LAST


def test_a_change_by_a_person_after_the_bot_comment_runs(bug_new, comments_returning):
    # A field change with no comment is enough.
    changed = quiet(bug_new, last_change_time_non_bot="2026-10-13T11:00:00Z")
    assert skip_reason(_state(changed, comments_returning[:3])) is None

    # The reporter answered: run.
    answered = quiet(bug_new, last_change_time_non_bot="2026-10-14T10:00:00Z")
    assert skip_reason(_state(answered, comments_returning)) is None


def test_a_person_with_the_newest_comment_runs_on_a_quiet_bug(bug_new, comments_new):
    # Nothing changed since the newest comment, but a person wrote it.
    bug = quiet(bug_new, last_change_time_non_bot="2026-10-11T08:00:00Z")
    assert skip_reason(_state(bug, comments_new)) is None


def test_another_hackbot_agents_comment_does_not_skip(bug_new, comments_returning):
    for tags in (["bug-fix"], []):
        other = dict(comments_returning[2], tags=tags)
        comments = [*comments_returning[:2], other]
        assert skip_reason(_state(quiet(bug_new), comments)) is None, tags


def test_the_old_nothing_human_skip_is_gone(bug_new, comments_returning):
    # A tagged comment from the bot, then a person's change: the old skip read the
    # tag and the human change time. The new one reads only the newest comment.
    person = dict(comments_returning[2], creator="dev@example.org")
    assert (
        skip_reason(_state(quiet(bug_new), [*comments_returning[:2], person])) is None
    )


def test_a_bug_that_became_closable_after_the_bot_comment_runs(
    bug_new, comments_returning
):
    # The staleness clock is the one thing that brings a quiet bug back.
    bug = with_needinfo(quiet(bug_new, flags=[]), "reporter@example.com")
    comments = comments_returning[:3]
    assert skip_reason(TriageState(bug=bug, comments=comments, now=LATER)) is None

    # The same needinfo, still fresh.
    assert skip_reason(_state(bug, comments)) == SKIP_BOT_LAST


def test_a_bot_comment_after_the_bug_became_closable_is_skipped(
    bug_new, comments_returning
):
    # The bot already ran on the closable bug and chose not to close it.
    again = dict(comments_returning[2], creation_time="2026-11-01T09:00:00Z")
    bug = with_needinfo(
        quiet(bug_new, flags=[], last_change_time_non_bot="2026-11-01T09:00:00Z"),
        "reporter@example.com",
    )
    state = TriageState(bug=bug, comments=[*comments_returning[:3], again], now=LATER)
    assert state.incomplete_allowed
    assert skip_reason(state) == SKIP_BOT_LAST


@pytest.mark.parametrize("assignee", ["dev@example.com", "Dev@Example.com"])
def test_an_assigned_bug_is_skipped(bug_new, comments_new, assignee):
    bug = dict(bug_new, assigned_to=assignee)
    assert skip_reason(_state(bug, comments_new)) == (
        "assigned to dev@example.com, who owns the bug"
    )


@pytest.mark.parametrize("assignee", ["nobody@mozilla.org", "", None])
def test_an_unassigned_bug_is_triaged(bug_new, comments_new, assignee):
    assert (
        skip_reason(_state(dict(bug_new, assigned_to=assignee), comments_new)) is None
    )


@pytest.mark.parametrize(
    "creator", ["reporter@example.com", "intermittent-bug-filer@mozilla.bugs"]
)
def test_a_bug_with_severity_and_priority_set_is_still_triaged(
    bug_new, comments_new, creator
):
    # The skip for a bug with both set is gone: Treeherder's filer sets S4 and P5, and
    # a person's levels do not mean that the bug needs no analysis.
    bug = dict(
        bug_new, creator=creator, creator_detail={}, severity="S4", priority="P5"
    )
    assert skip_reason(_state(bug, comments_new)) is None


def test_the_skips_are_checked_in_the_plan_order(bug_new, comments_new):
    # Scope first: an out-of-scope meta bug reports scope, so the reason names the
    # condition a human would fix first.
    bug = dict(bug_new, component="Graphics", keywords=["meta"])
    assert "out of scope" in skip_reason(_state(bug, comments_new))


def test_an_unreadable_bug_fails_the_run_so_it_is_retried(new_state):
    # A skip would be a successful run and use up the bug's dedupe key; a failed run
    # is retried by BugBot.
    with pytest.raises(BugUnreadable):
        require_state(None)
    assert require_state(new_state) is new_state
