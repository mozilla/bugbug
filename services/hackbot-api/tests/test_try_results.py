"""Tests for applying actions that wait on a try push.

Treeherder and the action handlers are faked, so these exercise the sweep's own
logic: counting runs, the failure-rate rule, the Slack thread, and what happens to
the waiting action in each outcome.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from app import try_results
from app.action_handlers.base import ActionResult
from app.actions_applier import SKIPPED, WAITING, initial_status

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
TASKS = ["condprof-win-full", "condprof-win-settled"]
WAIT = {
    "ref": "try",
    "max_failure_rate": 0.1,
    "slack_channel": "#perftest-intermittent-hackbot",
    "slack_ref": "announce",
}


@dataclass
class _Run:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent: str = "perftest-repair"
    created_at: datetime = NOW - timedelta(hours=2)


@dataclass
class _Row:
    ref: str | None
    type: str
    params: dict
    status: str
    run_id: uuid.UUID
    id: int = 0
    result: dict | None = None
    error: str | None = None
    applied_at: datetime | None = None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _DB:
    """A fake session answering every query with a run's rows.

    With ``list_waiting`` the first query instead lists the waiting rows, as
    ``check_try_pushes`` asks before checking each.
    """

    def __init__(self, run, rows, list_waiting=False):
        self.run = run
        self.rows = rows
        self.commits = 0
        self._listed = not list_waiting

    async def get(self, _model, _run_id):
        return self.run

    async def execute(self, _statement):
        if not self._listed:
            self._listed = True
            return _Result([r for r in self.rows if r.status == WAITING])
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _setup(try_status="applied", pushed_ago=timedelta(hours=1), try_error=None):
    run = _Run()
    try_row = _Row(
        ref="try",
        type="try_server.push",
        params={"tasks": TASKS, "rebuild": 10},
        status=try_status,
        run_id=run.run_id,
        id=1,
        result={"job_id": 42, "url": "https://th/try", "tasks": TASKS}
        if try_status == "applied"
        else None,
        error=try_error,
        applied_at=NOW - pushed_ago if try_status == "applied" else None,
    )
    announce = _Row(
        ref="announce",
        type="slack.post_message",
        params={"channel": "#perftest-intermittent-hackbot", "text": "pushed"},
        status="applied",
        run_id=run.run_id,
        id=2,
        result={"channel": "C1", "ts": "1700.1"},
    )
    patch = _Row(
        ref="patch",
        type="phabricator.submit_patch",
        params={
            "bug_id": 2077400,
            "title": "Bug 2077400 - Check for arsenic",
            "summary": "s",
            "test_plan": "Try push: {{actions.try.url}}",
            "reviewers": ["#perftest-reviewers"],
            "wait_for_try": WAIT,
        },
        status=WAITING,
        run_id=run.run_id,
        id=3,
    )
    return _DB(run, [try_row, announce, patch]), patch


def _jobs(**per_task):
    """``label=(passed, failed, running)`` -> Treeherder job dicts."""
    jobs = []
    for label, (passed, failed, running) in per_task.items():
        label = label.replace("_", "-")
        jobs += [
            {"job_type_name": label, "state": "completed", "result": "success"}
        ] * passed
        jobs += [
            {"job_type_name": label, "state": "completed", "result": "testfailed"}
        ] * failed
        jobs += [
            {"job_type_name": label, "state": "running", "result": "unknown"}
        ] * running
    return jobs


@pytest.fixture
def dispatched(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(run, action_type, params, attachments):
        calls.append((action_type, params))
        if action_type == "phabricator.submit_patch":
            return ActionResult.ok({"revision_id": 9, "url": "https://phab/D9"})
        return ActionResult.ok({"channel": "C1", "ts": "1700.2"})

    monkeypatch.setattr(try_results, "_dispatch", fake_dispatch)
    return calls


def _treeherder(monkeypatch, jobs):
    async def fake_fetch(lando_job_id):
        assert lando_job_id == 42
        return jobs

    monkeypatch.setattr(try_results, "fetch_try_jobs", fake_fetch)


def _slack(calls):
    return [params for kind, params in calls if kind == "slack.post_message"]


def test_wait_for_try_actions_start_waiting():
    assert initial_status({"wait_for_try": WAIT}) == WAITING
    assert initial_status({"bug_id": 1}) == "pending"


def test_tally_counts_each_tasks_runs():
    jobs = _jobs(condprof_win_full=(9, 1, 0), condprof_win_settled=(10, 0, 0))
    jobs.append(
        {"job_type_name": "condprof-win-full", "state": "completed", "result": "retry"}
    )
    jobs.append(
        {"job_type_name": "build-win64", "state": "completed", "result": "busted"}
    )
    outcome = try_results.tally(jobs, TASKS)
    assert outcome.complete
    assert (outcome.failed, outcome.total) == (1, 20)
    assert outcome.failure_rate == pytest.approx(0.05)


@pytest.mark.parametrize(
    "jobs",
    [
        _jobs(condprof_win_full=(9, 0, 1), condprof_win_settled=(10, 0, 0)),
        # A selected task with no runs yet is not finished either.
        _jobs(condprof_win_full=(10, 0, 0)),
    ],
)
def test_tally_is_incomplete_while_runs_are_missing(jobs):
    assert not try_results.tally(jobs, TASKS).complete


async def test_a_running_push_is_left_waiting(monkeypatch, dispatched):
    db, patch = _setup()
    _treeherder(monkeypatch, _jobs(condprof_win_full=(5, 0, 5)))

    await try_results._Sweep(db, NOW).check(patch)

    assert patch.status == WAITING
    assert dispatched == []


@pytest.mark.parametrize("failed", [0, 1, 2])
async def test_a_push_within_the_limit_opens_the_revision(
    monkeypatch, dispatched, failed
):
    db, patch = _setup()
    # 20 runs: up to 2 failures is at most 10%.
    _treeherder(
        monkeypatch,
        _jobs(
            condprof_win_full=(10 - failed, failed, 0), condprof_win_settled=(10, 0, 0)
        ),
    )

    await try_results._Sweep(db, NOW).check(patch)

    assert patch.status == "applied"
    assert patch.result == {"revision_id": 9, "url": "https://phab/D9"}
    submit = next(p for kind, p in dispatched if kind == "phabricator.submit_patch")
    assert "wait_for_try" not in submit
    assert submit["reviewers"] == ["#perftest-reviewers"]
    # The placeholder is resolved from the applied push, and the results added.
    assert submit["test_plan"].startswith("Try push: https://th/try")
    assert f"condprof-win-full: {10 - failed}/10 passed" in submit["test_plan"]

    results, outcome = _slack(dispatched)
    assert results["thread_ts"] == outcome["thread_ts"] == "1700.1"
    assert "Try jobs completed" in results["text"]
    assert (
        f"`condprof-win-full`: {10 - failed}/10 passed, {failed} failed"
        in results["text"]
    )
    assert (
        "opened <https://phab/D9|the revision> for review by #perftest-reviewers"
        in (outcome["text"])
    )
    assert outcome["reply_broadcast"] is True


async def test_a_push_above_the_limit_opens_nothing(monkeypatch, dispatched):
    db, patch = _setup()
    _treeherder(
        monkeypatch,
        _jobs(condprof_win_full=(7, 3, 0), condprof_win_settled=(10, 0, 0)),
    )

    await try_results._Sweep(db, NOW).check(patch)

    assert patch.status == SKIPPED
    assert "3/20 failed (15%)" in patch.error
    assert not any(kind == "phabricator.submit_patch" for kind, _ in dispatched)
    results, outcome = _slack(dispatched)
    assert "`condprof-win-full`: 7/10 passed, 3 failed" in results["text"]
    assert "no revision was opened" in outcome["text"]


async def test_a_failed_push_is_reported_and_dropped(monkeypatch, dispatched):
    db, patch = _setup(try_status="failed", try_error="Lando rejected the push")

    await try_results._Sweep(db, NOW).check(patch)

    assert patch.status == SKIPPED
    (message,) = _slack(dispatched)
    assert "Lando rejected the push" in message["text"]


async def test_a_push_that_never_finishes_is_dropped_after_a_day(
    monkeypatch, dispatched
):
    db, patch = _setup(pushed_ago=timedelta(hours=25))
    _treeherder(monkeypatch, _jobs(condprof_win_full=(5, 0, 5)))

    await try_results._Sweep(db, NOW).check(patch)

    assert patch.status == SKIPPED
    (message,) = _slack(dispatched)
    assert "did not finish within 24 hours" in message["text"]


async def test_one_unanswerable_push_does_not_stop_the_sweep(monkeypatch, dispatched):
    db, patch = _setup()

    async def broken_fetch(_lando_job_id):
        raise RuntimeError("treeherder down")

    monkeypatch.setattr(try_results, "fetch_try_jobs", broken_fetch)
    db = _DB(db.run, db.rows, list_waiting=True)

    result = await try_results.check_try_pushes(db, now=NOW)

    assert (result.considered, result.failed) == (1, 1)
    assert patch.status == WAITING
    assert dispatched == []


async def test_the_maintenance_endpoint_runs_the_sweep(monkeypatch):
    from app.routers import maintenance

    async def fake_check(db, *, limit):
        assert limit == 5
        return try_results.TryPushSweepResult(considered=2, still_running=1, applied=1)

    monkeypatch.setattr(maintenance.try_results, "check_try_pushes", fake_check)

    result = await maintenance.check_try_pushes(limit=5, db=object())

    assert result.model_dump() == {
        "considered": 2,
        "still_running": 1,
        "applied": 1,
        "not_applied": 0,
        "failed": 0,
    }
