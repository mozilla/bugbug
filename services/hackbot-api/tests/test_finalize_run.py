"""Tests for finalize_run.

The Eventarc-triggered /internal/events/agent-run-finished route calls
this instead of a client's GET /runs/{run_id} triggering it (see
app/routers/runs.py).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from app import gcs, jobs, pubsub
from app.jobs import ExecutionStatus
from app.routers.runs import finalize_run
from app.schemas import ArtifactRef, RunStatus, RunSummary


@dataclass
class _FakeRun:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent: str = "bug-fix"
    status: str = RunStatus.pending.value
    execution_name: str | None = "projects/p/locations/l/jobs/j/executions/e"
    artifacts: list = field(default_factory=list)
    summary: dict | None = None
    error: str | None = None
    finalized_at: datetime | None = None


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.fixture(autouse=True)
def _no_publish(monkeypatch):
    published = []

    async def fake_publish(run_id, agent, status):
        published.append((run_id, agent, status))

    monkeypatch.setattr(pubsub, "publish_run_completed", fake_publish)
    return published


async def test_noop_when_already_finalized(monkeypatch):
    run = _FakeRun(finalized_at=datetime.now(timezone.utc))
    db = _FakeDB()

    async def fail(*_a, **_k):
        raise AssertionError("should not check execution status once finalized")

    monkeypatch.setattr(jobs, "get_execution_status", fail)
    await finalize_run(db, run)
    assert db.commits == 0


def _async(value):
    """An async callable that ignores its args and returns `value`."""

    async def _fn(*_args, **_kwargs):
        return value

    return _fn


async def test_transitions_pending_to_running(monkeypatch):
    run = _FakeRun(status=RunStatus.pending.value)
    db = _FakeDB()
    monkeypatch.setattr(jobs, "get_execution_status", _async(ExecutionStatus.running))
    await finalize_run(db, run)
    assert run.status == RunStatus.running.value
    assert run.finalized_at is None
    assert db.commits == 1


async def test_finalizes_succeeded_run(monkeypatch, _no_publish):
    run = _FakeRun()
    db = _FakeDB()
    monkeypatch.setattr(jobs, "get_execution_status", _async(ExecutionStatus.succeeded))
    monkeypatch.setattr(gcs, "read_summary", _async(RunSummary(status="ok")))
    monkeypatch.setattr(
        gcs, "list_artifacts", _async([ArtifactRef(name="summary.json", size=10)])
    )

    await finalize_run(db, run)

    assert run.status == RunStatus.succeeded.value
    assert run.finalized_at is not None
    assert run.artifacts == [{"name": "summary.json", "size": 10, "content_type": None}]
    assert _no_publish == [(str(run.run_id), run.agent, RunStatus.succeeded.value)]


async def test_finalizes_as_failed_when_summary_missing(monkeypatch):
    run = _FakeRun()
    db = _FakeDB()
    monkeypatch.setattr(jobs, "get_execution_status", _async(ExecutionStatus.succeeded))
    monkeypatch.setattr(gcs, "read_summary", _async(None))
    monkeypatch.setattr(gcs, "list_artifacts", _async([]))

    await finalize_run(db, run)

    assert run.status == RunStatus.failed.value
    assert "summary.json" in run.error
    assert run.finalized_at is not None


async def test_cancelled_execution_marks_timed_out(monkeypatch):
    run = _FakeRun()
    db = _FakeDB()
    monkeypatch.setattr(jobs, "get_execution_status", _async(ExecutionStatus.cancelled))
    monkeypatch.setattr(gcs, "read_summary", _async(None))
    monkeypatch.setattr(gcs, "list_artifacts", _async([]))

    await finalize_run(db, run)

    assert run.status == RunStatus.timed_out.value
    assert run.finalized_at is not None


async def test_second_call_is_noop_after_finalizing(monkeypatch):
    run = _FakeRun()
    db = _FakeDB()
    calls = []

    async def fake_status(name):
        calls.append(name)
        return ExecutionStatus.succeeded

    monkeypatch.setattr(jobs, "get_execution_status", fake_status)
    monkeypatch.setattr(gcs, "read_summary", _async(RunSummary(status="ok")))
    monkeypatch.setattr(gcs, "list_artifacts", _async([]))

    await finalize_run(db, run)
    await finalize_run(db, run)

    assert len(calls) == 1
