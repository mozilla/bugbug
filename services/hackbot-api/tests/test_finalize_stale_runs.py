"""Tests for the stale-run reconciliation sweep.

The sweep is the safety net that makes a lost completion event recoverable:
without it, a run leaves `pending` only if its single Cloud Run completion
event is delivered and handled (see STUCK-PENDING-RUNS.md).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from app.routers import maintenance
from app.routers.maintenance import finalize_stale_runs
from app.schemas import RunStatus


@dataclass
class _FakeRun:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent: str = "autowebcompat-repro"
    status: str = RunStatus.pending.value
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) - timedelta(days=60)
    )
    execution_name: str | None = "projects/p/locations/l/jobs/j/executions/e"
    finalized_at: datetime | None = None


class _FakeResult:
    def __init__(self, runs):
        self._runs = runs

    def scalars(self):
        return iter(self._runs)


class _FakeDB:
    def __init__(self, runs):
        self._runs = runs
        self.rollbacks = 0
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self._runs)

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def _finalizer(monkeypatch):
    """Replace finalize_run with a recorder whose behaviour tests can set."""
    calls = []

    def install(behaviour):
        async def fake_finalize(_db, run):
            calls.append(run.run_id)
            behaviour(run)

        monkeypatch.setattr(maintenance, "finalize_run", fake_finalize)
        return calls

    return install


def _finalizes(run):
    run.status = RunStatus.succeeded.value
    run.finalized_at = datetime.now(timezone.utc)


async def test_finalizes_stale_runs(_finalizer):
    runs = [_FakeRun(), _FakeRun()]
    db = _FakeDB(runs)
    calls = _finalizer(_finalizes)

    result = await finalize_stale_runs(min_age_minutes=120, limit=100, db=db)

    assert calls == [run.run_id for run in runs]
    assert result.finalized == [run.run_id for run in runs]
    assert result.still_running == []
    assert result.errored == []


async def test_dry_run_touches_nothing(_finalizer):
    runs = [_FakeRun()]
    db = _FakeDB(runs)
    calls = _finalizer(_finalizes)

    result = await finalize_stale_runs(
        min_age_minutes=120, limit=100, dry_run=True, db=db
    )

    assert calls == []
    assert result.considered == [runs[0].run_id]
    assert result.finalized == []


async def test_still_running_runs_are_reported_not_finalized(_finalizer):
    """finalize_run returns without writing while an execution is in flight."""
    runs = [_FakeRun()]
    db = _FakeDB(runs)
    _finalizer(lambda run: None)

    result = await finalize_stale_runs(min_age_minutes=120, limit=100, db=db)

    assert result.finalized == []
    assert result.still_running == [runs[0].run_id]


async def test_one_failure_does_not_abort_the_sweep(monkeypatch):
    bad, good = _FakeRun(), _FakeRun()
    db = _FakeDB([bad, good])

    async def fake_finalize(_db, run):
        if run.run_id == bad.run_id:
            raise RuntimeError("boom")
        _finalizes(run)

    monkeypatch.setattr(maintenance, "finalize_run", fake_finalize)

    result = await finalize_stale_runs(min_age_minutes=120, limit=100, db=db)

    assert result.errored == [bad.run_id]
    assert result.finalized == [good.run_id]
    assert db.rollbacks == 1
