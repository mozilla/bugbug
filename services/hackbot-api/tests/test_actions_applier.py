"""Tests for the action applier.

Covers the {{actions.<ref>.<field>}} placeholder resolver, the succeeded-run
gate + per-agent auto-apply opt-in in `on_run_completed`, and the manual
`apply_all_pending` path — see app/actions_applier.py.
"""

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

from app import actions_applier
from app.actions_applier import (
    apply_all_pending,
    on_run_completed,
    resolve_placeholders,
)
from app.schemas import RunStatus


def test_resolves_known_ref_and_field():
    out = resolve_placeholders(
        "Fix submitted: {{actions.patch.revision_url}}",
        {"patch": {"revision_url": "https://phabricator.services.mozilla.com/D1"}},
    )
    assert out == "Fix submitted: https://phabricator.services.mozilla.com/D1"


def test_unknown_ref_left_as_is():
    out = resolve_placeholders("See {{actions.missing.url}}", {})
    assert out == "See {{actions.missing.url}}"


def test_unknown_field_left_as_is():
    out = resolve_placeholders(
        "See {{actions.patch.nope}}", {"patch": {"revision_url": "x"}}
    )
    assert out == "See {{actions.patch.nope}}"


def test_recurses_into_dict_and_list():
    value = {
        "text": "{{actions.patch.revision_url}}",
        "items": ["{{actions.patch.revision_id}}", "plain"],
    }
    out = resolve_placeholders(
        value, {"patch": {"revision_url": "u", "revision_id": 5}}
    )
    assert out == {"text": "u", "items": ["5", "plain"]}


def test_non_string_values_pass_through():
    assert resolve_placeholders(42, {}) == 42
    assert resolve_placeholders(None, {}) is None
    assert resolve_placeholders(True, {}) is True


def test_multiple_placeholders_in_one_string():
    out = resolve_placeholders(
        "{{actions.a.x}} and {{actions.b.y}}",
        {"a": {"x": "1"}, "b": {"y": "2"}},
    )
    assert out == "1 and 2"


# --- record / auto-apply gating --------------------------------------- #


@dataclass
class _FakeRun:
    status: str
    agent: str = "bug-fix"
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    summary: dict | None = None


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def execute(self, *a, **k):
        raise AssertionError(
            "ensure_action_rows should be monkeypatched in these tests"
        )


def _patch_applier(monkeypatch, *, auto: bool | None):
    """Stub ensure/apply and the registry; record what got called.

    `auto=None` means the agent isn't in the registry at all.
    """
    calls = {"ensured": False, "applied": False}

    async def fake_ensure(db, run):
        calls["ensured"] = True
        return [("row", [])]

    async def fake_apply(db, run, rows):
        calls["applied"] = True

    monkeypatch.setattr(actions_applier, "ensure_action_rows", fake_ensure)
    monkeypatch.setattr(actions_applier, "_apply_pending_rows", fake_apply)
    registry = (
        {} if auto is None else {"bug-fix": SimpleNamespace(auto_apply_actions=auto)}
    )
    monkeypatch.setattr(actions_applier, "AGENT_REGISTRY", registry)
    return calls


async def test_non_succeeded_run_records_nothing(monkeypatch):
    calls = _patch_applier(monkeypatch, auto=True)
    for status in (RunStatus.failed.value, RunStatus.timed_out.value):
        await on_run_completed(_FakeDB(), _FakeRun(status=status))
    assert calls == {"ensured": False, "applied": False}


async def test_succeeded_opted_in_agent_records_and_applies(monkeypatch):
    calls = _patch_applier(monkeypatch, auto=True)
    db = _FakeDB()
    await on_run_completed(db, _FakeRun(status=RunStatus.succeeded.value))
    assert calls == {"ensured": True, "applied": True}
    assert db.commits >= 1


async def test_succeeded_non_opted_agent_records_but_does_not_apply(monkeypatch):
    calls = _patch_applier(monkeypatch, auto=False)
    db = _FakeDB()
    await on_run_completed(db, _FakeRun(status=RunStatus.succeeded.value))
    assert calls == {"ensured": True, "applied": False}
    assert db.commits >= 1


async def test_succeeded_unknown_agent_does_not_apply(monkeypatch):
    calls = _patch_applier(monkeypatch, auto=None)
    await on_run_completed(_FakeDB(), _FakeRun(status=RunStatus.succeeded.value))
    assert calls == {"ensured": True, "applied": False}


async def test_apply_all_pending_always_applies(monkeypatch):
    # Manual apply ignores the opt-in flag entirely.
    calls = _patch_applier(monkeypatch, auto=False)
    await apply_all_pending(_FakeDB(), _FakeRun(status=RunStatus.succeeded.value))
    assert calls == {"ensured": True, "applied": True}
