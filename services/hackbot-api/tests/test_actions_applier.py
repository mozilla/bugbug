"""Tests for the action applier.

Covers the {{actions.<ref>.<field>}} placeholder resolver (the mechanism that
lets a later action reference an earlier one's apply-time result) and the
succeeded-run-only gate — see app/actions_applier.py.
"""

import uuid
from dataclasses import dataclass, field

from app.actions_applier import apply_pending_actions, resolve_placeholders
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


# --- succeeded-run-only gate ------------------------------------------- #


@dataclass
class _FakeRun:
    status: str
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    summary: dict | None = None


class _FakeDB:
    """Fails loudly if touched — a skipped run must not query/write anything."""

    async def execute(self, *a, **k):
        raise AssertionError(
            "apply_pending_actions must not touch the DB when skipping"
        )


async def test_skips_non_succeeded_runs():
    # An action recorded on a failed run must NOT be applied. _FakeDB raises
    # if apply_pending_actions gets past the status gate to _ensure_rows.
    for status in (RunStatus.failed.value, RunStatus.timed_out.value):
        run = _FakeRun(
            status=status,
            summary={"actions": [{"type": "bugzilla.add_comment", "params": {}}]},
        )
        await apply_pending_actions(_FakeDB(), run)  # must not raise
