"""Tests for GET /runs listing: filtering, offset paging, and ordering.

Follows this suite's fake-DB style — the handler is called directly with a fake
session that captures the SQLAlchemy statement, so we can assert the query it
builds (WHERE / ORDER BY / OFFSET) without a real Postgres.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.routers import runs as runs_router
from app.schemas import RunStatus
from sqlalchemy.dialects import postgresql


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


class _CapturingDB:
    """Records the statement passed to execute() and returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return _Result(self._rows)


def _fake_run(**overrides):
    base = dict(
        run_id=uuid.uuid4(),
        agent="frontend-triage",
        status="succeeded",
        inputs={"bug_id": 123},
        requested_by=None,
        created_at=datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 21, 12, 5, tzinfo=timezone.utc),
        execution_name=None,
        results_prefix="results/abc/",
        summary=None,
        artifacts=[],
        error=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_list_runs_default_orders_and_pages():
    db = _CapturingDB([_fake_run()])
    out = await runs_router.list_runs(
        limit=50, offset=0, agent=None, status_filter=None, requested_by=None, db=db
    )
    sql = _sql(db.stmt)
    assert "FROM runs" in sql
    assert "WHERE" not in sql  # no filters applied
    assert "ORDER BY runs.created_at DESC" in sql
    assert "runs.run_id DESC" in sql  # deterministic tiebreaker
    assert "LIMIT" in sql
    assert "OFFSET" in sql
    assert len(out) == 1 and out[0].agent == "frontend-triage"


async def test_list_runs_filters_by_agent_and_status():
    db = _CapturingDB([])
    await runs_router.list_runs(
        limit=10,
        offset=20,
        agent="bug-fix",
        status_filter=RunStatus.failed,
        requested_by=None,
        db=db,
    )
    sql = _sql(db.stmt)
    assert "runs.agent =" in sql
    assert "runs.status =" in sql
    assert "OFFSET" in sql


async def test_list_runs_filters_by_agent_only():
    db = _CapturingDB([])
    await runs_router.list_runs(
        limit=50,
        offset=0,
        agent="frontend-triage",
        status_filter=None,
        requested_by=None,
        db=db,
    )
    sql = _sql(db.stmt)
    assert "runs.agent =" in sql
    assert "runs.status =" not in sql


async def test_list_runs_filters_by_requested_by():
    db = _CapturingDB([])
    await runs_router.list_runs(
        limit=50,
        offset=0,
        agent=None,
        status_filter=None,
        requested_by="someone@mozilla.com",
        db=db,
    )
    sql = _sql(db.stmt)
    assert "runs.requested_by =" in sql
    params = db.stmt.compile(dialect=postgresql.dialect()).params
    assert "someone@mozilla.com" in params.values()


async def test_list_runs_without_requested_by_is_unfiltered():
    db = _CapturingDB([])
    await runs_router.list_runs(
        limit=50, offset=0, agent=None, status_filter=None, requested_by=None, db=db
    )
    # "runs.requested_by" also appears in the SELECT list, so assert on the clause.
    assert "WHERE" not in _sql(db.stmt)


# The `requested_by` query param is normalized by its annotation (see `UserEmail`
# in app/routers/runs.py), which only runs inside FastAPI's request handling --
# hence the TestClient here rather than a direct handler call.


@pytest.mark.parametrize(
    "raw, expected",
    [("Someone@Mozilla.com", "someone@mozilla.com"), ("  a@b.c ", "a@b.c")],
)
def test_list_runs_normalizes_requested_by_query_param(client, db, raw, expected):
    assert client.get("/runs", params={"requested_by": raw}).status_code == 200
    params = db.stmt.compile(dialect=postgresql.dialect()).params
    assert expected in params.values()


@pytest.mark.parametrize("raw", ["", "   "])
def test_list_runs_ignores_blank_requested_by(client, db, raw):
    # A blank param means "no filter", not "runs with an empty requester".
    assert client.get("/runs", params={"requested_by": raw}).status_code == 200
    assert "WHERE" not in _sql(db.stmt)
