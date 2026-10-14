"""Tests for the run-actions HTTP endpoints (list + manual apply).

Exercises the route handlers directly with a fake DB (matching this suite's
fake-based style), stubbing the applier/query helpers to keep the handlers'
own logic — 404 handling, calling apply_all_pending, returning the list — in
focus.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.actions_applier import ActionNotFound, UnappliedDependency
from app.routers import runs as runs_router
from app.schemas import RunActionDoc
from fastapi import HTTPException


class _FakeDB:
    def __init__(self, run):
        self._run = run

    async def get(self, model, run_id):
        return self._run


_ACTIONS = [
    RunActionDoc(idx=0, type="bugzilla.add_comment", params={}, status="pending")
]


async def test_list_run_actions_404():
    with pytest.raises(HTTPException) as exc:
        await runs_router.list_run_actions(uuid.uuid4(), _FakeDB(None))
    assert exc.value.status_code == 404


async def test_list_run_actions_returns_rows(monkeypatch):
    async def fake_list(db, run_id):
        return _ACTIONS

    monkeypatch.setattr(runs_router, "_list_actions", fake_list)
    out = await runs_router.list_run_actions(uuid.uuid4(), _FakeDB(SimpleNamespace()))
    assert out is _ACTIONS


async def test_apply_run_actions_404():
    with pytest.raises(HTTPException) as exc:
        await runs_router.apply_run_actions(uuid.uuid4(), _FakeDB(None))
    assert exc.value.status_code == 404


async def test_apply_run_actions_applies_then_returns(monkeypatch):
    applied = {"called": False}

    async def fake_apply(db, run):
        applied["called"] = True

    async def fake_list(db, run_id):
        return _ACTIONS

    monkeypatch.setattr(runs_router, "apply_all_pending", fake_apply)
    monkeypatch.setattr(runs_router, "_list_actions", fake_list)

    out = await runs_router.apply_run_actions(uuid.uuid4(), _FakeDB(SimpleNamespace()))
    assert applied["called"] is True
    assert out is _ACTIONS


async def test_apply_run_action_applies_then_returns(monkeypatch):
    calls = []

    async def fake_apply(db, run, idx):
        calls.append(idx)

    async def fake_list(db, run_id):
        return _ACTIONS

    monkeypatch.setattr(runs_router, "apply_one", fake_apply)
    monkeypatch.setattr(runs_router, "_list_actions", fake_list)

    out = await runs_router.apply_run_action(
        uuid.uuid4(), 3, _FakeDB(SimpleNamespace())
    )
    assert calls == [3]
    assert out is _ACTIONS


@pytest.mark.parametrize(
    "exc, status", [(ActionNotFound("x"), 404), (UnappliedDependency("x"), 409)]
)
async def test_apply_run_action_errors(monkeypatch, exc, status):
    async def fake_apply(db, run, idx):
        raise exc

    monkeypatch.setattr(runs_router, "apply_one", fake_apply)
    with pytest.raises(HTTPException) as err:
        await runs_router.apply_run_action(uuid.uuid4(), 0, _FakeDB(SimpleNamespace()))
    assert err.value.status_code == status
