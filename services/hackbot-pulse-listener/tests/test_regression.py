"""Tests for the build-failure regression gate (is_new_build_failure)."""

from unittest.mock import patch

import pytest
from app import regression
from mozci.errors import ParentPushNotFound

LABEL = "build-linux64/opt"
OTHER_LABEL = "build-macosx64/opt"


def _job(result="success", state="completed", task_id="T"):
    return {"result": result, "state": state, "task_id": task_id}


def passed():
    return [_job(result="success")]


def failed():
    return [_job(result="testfailed")]


def busted():
    return [_job(result="busted")]


def running():
    return [_job(result=None, state="running")]


def retried():
    return [_job(result="retry")]


def never_ran():
    return []


class FakePush:
    """A push whose only job is to yield the ancestor chain, as mozci does."""

    def __init__(self, rev, parent=None):
        self.rev = rev
        self._parent = parent

    @property
    def parent(self):
        if self._parent is None:
            raise ParentPushNotFound("no parent", rev=self.rev, branch="autoland")
        return self._parent


def _chain(*ancestors):
    """Ancestor revs rev1..revN, and the Treeherder jobs recorded on each."""
    push = None
    for i in range(len(ancestors), 0, -1):
        push = FakePush(f"rev{i}", parent=push)
    jobs = {f"rev{i + 1}": a for i, a in enumerate(ancestors)}
    return FakePush("head", parent=push), jobs


def _run(chain, *, poll=None):
    """Run the gate. `poll` supplies later job snapshots, one per poll attempt."""
    head, jobs = chain
    snapshots = [jobs] + list(poll or [])
    state = {"attempt": 0}

    def label_jobs(project, rev, label):
        snapshot = snapshots[min(state["attempt"], len(snapshots) - 1)]
        return [j for j in snapshot.get(rev, []) if label == LABEL]

    def bump(_seconds):
        state["attempt"] += 1

    with (
        patch.object(regression, "Push", return_value=head),
        patch.object(regression.treeherder, "label_jobs", label_jobs),
        patch.object(regression.time, "sleep", bump),
    ):
        return regression.is_new_build_failure("autoland", "head", LABEL)


def test_parent_passed_is_new_failure():
    assert _run(_chain(passed())) is True


def test_parent_failed_is_inherited():
    assert _run(_chain(failed())) is False


def test_parent_busted_is_inherited():
    assert _run(_chain(busted())) is False


def test_parent_with_one_green_retrigger_is_new_failure():
    # Any green run wins, which errs toward running the agent.
    assert _run(_chain([_job(result="testfailed"), _job(result="success")])) is True


def test_coalesced_parent_then_green_grandparent_is_new_failure():
    assert _run(_chain(never_ran(), passed())) is True


def test_coalesced_parent_then_failed_grandparent_is_inherited():
    assert _run(_chain(never_ran(), failed())) is False


def test_running_parent_is_waited_then_inherited():
    # A queued or running ancestor is polled until it settles.
    assert _run(_chain(running()), poll=[{"rev1": failed()}]) is False


@pytest.mark.parametrize("result", ["retry", "exception", "unknown"])
def test_unsettled_result_is_waited_not_skipped(result):
    # These may still change outcome, so the ancestor must be waited for. Asserting
    # the *inherited* verdict is what distinguishes waiting from treating the
    # ancestor as non-decisive and walking past it.
    chain = _chain([_job(result=result)])
    assert _run(chain, poll=[{"rev1": failed()}]) is False


def test_retried_parent_that_turns_green_is_a_new_failure():
    assert _run(_chain(retried()), poll=[{"rev1": passed()}]) is True


def test_unsettled_parent_past_deadline_runs_agent():
    head, jobs = _chain(running())
    with (
        patch.object(regression, "Push", return_value=head),
        patch.object(
            regression.treeherder,
            "label_jobs",
            lambda p, rev, label: jobs.get(rev, []),
        ),
        patch.object(regression.time, "sleep"),
        patch.object(
            regression.time,
            "monotonic",
            side_effect=[0.0, regression.MAX_WAIT_SECONDS + 1],
        ),
    ):
        assert regression.is_new_build_failure("autoland", "head", LABEL) is True


def test_no_parent_runs_agent():
    assert _run(_chain()) is True


def test_no_decisive_ancestor_runs_agent():
    assert _run(_chain(*[never_ran() for _ in range(regression.MAX_DEPTH + 2)])) is True


def test_other_label_on_parent_is_ignored():
    # Only our own label may decide: another build failing on the parent is not
    # our failure being inherited.
    head, _ = _chain(failed())
    with (
        patch.object(regression, "Push", return_value=head),
        patch.object(
            regression.treeherder,
            "label_jobs",
            lambda p, rev, label: failed() if label == OTHER_LABEL else [],
        ),
        patch.object(regression.time, "sleep"),
    ):
        assert regression.is_new_build_failure("autoland", "head", LABEL) is True


@pytest.mark.parametrize("boom", ["push", "jobs"])
def test_lookup_error_runs_agent(boom):
    def explode(*args, **kwargs):
        raise RuntimeError("upstream down")

    head, jobs = _chain(passed())
    with (
        patch.object(
            regression, "Push", explode if boom == "push" else lambda *a, **k: head
        ),
        patch.object(regression.treeherder, "label_jobs", explode),
        patch.object(regression.time, "sleep"),
    ):
        assert regression.is_new_build_failure("autoland", "head", LABEL) is True


def test_pending_notice_is_logged_once_not_every_poll(caplog):
    # A pending ancestor is re-polled for up to an hour; the notice must not repeat
    # at INFO on every attempt.
    chain = _chain(running())
    with caplog.at_level("DEBUG", logger="app.regression"):
        assert _run(chain, poll=[{"rev1": running()}, {"rev1": failed()}]) is False

    notices = [r for r in caplog.records if "not settled" in r.message]
    assert [r.levelname for r in notices] == ["INFO", "DEBUG"]


DAY = 24 * 60 * 60


class DatedPush:
    def __init__(self, date):
        self.date = date


def _stale(push_date, now, max_age_seconds=DAY):
    with (
        patch.object(regression, "Push", return_value=DatedPush(push_date)),
        patch.object(regression.time, "time", return_value=now),
    ):
        return regression.is_stale_push("autoland", "rev", max_age_seconds)


def test_push_from_minutes_ago_is_not_stale():
    assert _stale(push_date=1_000_000, now=1_000_000 + 30 * 60) is False


def test_push_from_seventeen_days_ago_is_stale():
    # Bug 6395: a backfill made a 17-day-old push look like a fresh failure.
    assert _stale(push_date=1_000_000, now=1_000_000 + 17 * DAY) is True


def test_push_age_limit_is_inclusive_of_the_limit():
    assert _stale(push_date=0, now=DAY) is False
    assert _stale(push_date=0, now=DAY + 1) is True


def test_unreadable_push_date_is_not_stale():
    # Fails open so a network blip never drops a real regression.
    with patch.object(regression, "Push", side_effect=RuntimeError("boom")):
        assert regression.is_stale_push("autoland", "rev", DAY) is False
