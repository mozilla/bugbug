"""Tests for the action applier.

Covers the {{actions.<ref>.<field>}} placeholder resolver, the succeeded-run
gate + per-agent auto-apply opt-in in `on_run_completed`, and the manual
`apply_all_pending` path — see app/actions_applier.py.
"""

import logging
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
        "Fix submitted: {{actions.patch.url}}",
        {"patch": {"url": "https://phabricator.services.mozilla.com/D1"}},
    )
    assert out == "Fix submitted: https://phabricator.services.mozilla.com/D1"


def test_unknown_ref_left_as_is(caplog):
    with caplog.at_level(logging.WARNING):
        out = resolve_placeholders("See {{actions.missing.url}}", {})
    assert out == "See {{actions.missing.url}}"
    assert "Unresolved action reference {{actions.missing.url}}" in caplog.text


def test_unknown_field_left_as_is(caplog):
    with caplog.at_level(logging.WARNING):
        out = resolve_placeholders(
            "See {{actions.patch.nope}}", {"patch": {"url": "x"}}
        )
    assert out == "See {{actions.patch.nope}}"
    assert "Unresolved action reference {{actions.patch.nope}}" in caplog.text


def test_recurses_into_dict_and_list():
    value = {
        "text": "{{actions.patch.url}}",
        "items": ["{{actions.patch.revision_id}}", "plain"],
    }
    out = resolve_placeholders(value, {"patch": {"url": "u", "revision_id": 5}})
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
    inputs: dict = field(default_factory=dict)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _FakeDB:
    """Enough of `AsyncSession` for the apply path.

    Keeps the row's *database* state separately from the caller's in-memory copy. That
    gap is deliberate: the rows are in the real session's identity map, so a lock that
    forgets `populate_existing` would judge its own pre-lock copy and re-dispatch an
    action another delivery already applied. A fake that returned the caller's own
    objects could never catch that — and didn't, until it did.

    `rows=None` means no statement should be reached, which is what the
    `on_run_completed` tests want: they stub the apply pass out entirely.
    """

    def __init__(self, rows=None):
        self.commits = 0
        self.claims = []
        self._identity = {row.id: row for row in rows or []}
        self._db_state = {row.id: {"status": row.status} for row in rows or []}
        # What each row looked like when we last handed it over, so `commit` can write
        # back only what this "transaction" actually changed — a real session flushes
        # dirty attributes, not every loaded row.
        self._handed_over = dict(self._db_state)

    def set_db_status(self, row_id, status):
        """Let a test say "another delivery got here first" without touching `row`."""
        self._db_state[row_id] = {"status": status}

    async def commit(self):
        self.commits += 1
        for row_id, row in self._identity.items():
            if row_id not in self._db_state:
                continue
            was = self._handed_over.get(row_id, {})
            current = {"status": row.status}
            # Only fields this session changed since it last read the row.
            changed = {k: v for k, v in current.items() if was.get(k) != v}
            if changed:
                self._db_state[row_id] = {**self._db_state[row_id], **changed}
            self._handed_over[row_id] = current

    async def execute(self, statement):
        compiled = statement.compile()
        sql = str(compiled)
        locking = "FOR UPDATE" in sql
        if locking and not statement.get_execution_options().get("populate_existing"):
            raise AssertionError(
                "the lock must re-read under it (populate_existing), or it judges the "
                "caller's stale copy"
            )
        if not locking and not statement.get_execution_options().get(
            "populate_existing"
        ):
            raise AssertionError(f"unexpected statement: {sql}")

        wanted = set()
        for key, value in compiled.params.items():
            if key.startswith("id_"):
                wanted.update(value if isinstance(value, (list, tuple)) else [value])
        # A re-read selects by run_id, not by row id: hand back everything.
        if not wanted:
            wanted = set(self._db_state)
        elif locking:
            self.claims.append(sorted(wanted))

        # Refreshing the identity-mapped object from the row is what
        # `populate_existing` does; the caller then judges current state.
        rows = []
        for row_id in sorted(wanted):
            if row_id not in self._db_state:
                continue
            row = self._identity[row_id]
            row.status = self._db_state[row_id]["status"]
            self._handed_over[row_id] = dict(self._db_state[row_id])
            rows.append(row)
        return _FakeResult(rows)


def _patch_applier(monkeypatch, *, auto: bool | None):
    """Stub ensure/apply and the registry; record what got called.

    `auto=None` means the agent isn't in the registry at all.
    """
    calls = {"ensured": False, "applied": False}
    rows = [(_row(0, "pending"), [])]

    async def fake_ensure(db, run):
        calls["ensured"] = True
        return rows

    async def fake_apply(db, run, rows):
        calls["applied"] = True
        for row, _ in rows:
            row.status = "applied"

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


# --- retry semantics: _apply_pending_rows re-attempts failed rows ------ #


class _RecordingHandler:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def apply(self, params, ctx):
        self.calls.append(params)
        return self.outcome


def _row(
    idx,
    status,
    *,
    action_type="bugzilla.add_comment",
    params=None,
    ref=None,
    result=None,
    error=None,
    applied_at=None,
):
    return SimpleNamespace(
        id=idx + 1,
        idx=idx,
        type=action_type,
        params=params if params is not None else {},
        ref=ref,
        status=status,
        result=result,
        error=error,
        applied_at=applied_at,
    )


async def test_apply_pending_rows_retries_failed_and_skips_applied(monkeypatch):
    # A manual re-apply retries a previously-failed action (this is what the
    # UI's retry button relies on) while leaving already-applied rows alone.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"ok": 1}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    applied = _row(0, "applied", result={"pre": 1}, applied_at="then")
    failed = _row(1, "failed", error="boom")
    pending = _row(2, "pending")
    rows = [(applied, []), (failed, []), (pending, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]),
        _FakeRun(status=RunStatus.succeeded.value),
        rows,
    )

    # The already-applied row is untouched; its handler never runs.
    assert applied.status == "applied" and applied.result == {"pre": 1}
    # The failed and pending rows are both (re)applied, clearing the stale error.
    assert len(handler.calls) == 2
    assert failed.status == "applied" and failed.error is None
    assert pending.status == "applied"


async def test_a_row_claimed_elsewhere_is_not_dispatched_again(monkeypatch):
    # The failure this claim exists for: Pub/Sub push is at-least-once *and*
    # concurrent, so two deliveries can both read the same `pending` row. Without a
    # claim both would call the handler and the bug would get two comments.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"ok": 1}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    row = _row(0, "pending")
    rows = [(row, [])]
    db = _FakeDB([row])
    # Another delivery got there first and committed. Set on the *database* state only:
    # the caller's `row` still reads `pending`, which is exactly what the lock has to
    # see through — it blocks until that delivery commits, then reads what it wrote.
    db.set_db_status(row.id, "applied")
    assert row.status == "pending"

    await actions_applier._apply_pending_rows(
        db, _FakeRun(status=RunStatus.succeeded.value), rows
    )

    assert handler.calls == []


async def test_the_lock_is_held_across_the_handler(monkeypatch):
    # Ordering is the whole point: the row must still be locked while Bugzilla is being
    # written, since that is the window a second delivery would otherwise write in. The
    # result is only committed afterwards.
    seen = {}
    row = _row(0, "pending")

    class _Handler:
        async def apply(self, params, ctx):
            seen["commits_at_dispatch"] = db.commits
            seen["locked"] = db.claims == [[row.id]]
            return SimpleNamespace(status="applied", result=None, error=None)

    monkeypatch.setattr(actions_applier, "get_handler", lambda t: _Handler())
    db = _FakeDB([row])
    await actions_applier._apply_pending_rows(
        db, _FakeRun(status=RunStatus.succeeded.value), [(row, [])]
    )

    assert seen["locked"], "dispatched without locking the row"
    assert seen["commits_at_dispatch"] == 0, "released the lock before dispatching"
    assert row.status == "applied"
    assert db.commits == 1


async def test_an_unresolved_reference_is_never_posted(monkeypatch):
    # The row it referenced failed, so the placeholder can't be substituted. Sending anyway puts a literal `{{actions.patch.url}}` in a real bug
    # comment with only a log line as the signal; it must fail visibly instead.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"url": "http://x/D1"}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    patch_row = _row(
        0, "pending", action_type="phabricator.submit_patch", ref="patch", params={}
    )
    comment = _row(
        1,
        "pending",
        params={"bug_id": 5, "text": "Fix: {{actions.patch.url}}"},
    )
    rows = [(patch_row, []), (comment, [])]
    db = _FakeDB([patch_row, comment])
    # The referenced row was applied elsewhere, so its result never lands here.
    db.set_db_status(patch_row.id, "applied")

    await actions_applier._apply_pending_rows(
        db, _FakeRun(status=RunStatus.succeeded.value), rows
    )

    assert handler.calls == []  # nothing was sent
    assert comment.status == "failed"
    assert "{{actions.patch.url}}" in comment.error


async def test_a_resolvable_reference_is_still_substituted(monkeypatch):
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"url": "http://x/D1"}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    patch_row = _row(
        0, "pending", action_type="phabricator.submit_patch", ref="patch", params={}
    )
    comment = _row(
        1, "pending", params={"bug_id": 5, "text": "Fix: {{actions.patch.url}}"}
    )
    rows = [(patch_row, []), (comment, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([patch_row, comment]),
        _FakeRun(status=RunStatus.succeeded.value),
        rows,
    )

    assert handler.calls[-1] == {"bug_id": 5, "text": "Fix: http://x/D1"}
    assert comment.status == "applied"


async def test_an_unhashable_bug_id_does_not_fail_the_whole_route(monkeypatch):
    # `plan_coalesced_groups` buckets by `bug_id`, so a list raises while *planning* —
    # before any per-action guard. Uncaught that 5xxs the push route, and with no
    # dead-letter topic the same message returns for the whole retention window. It
    # must degrade to applying rows singly instead.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result=None, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    bad = _row(0, "pending", params={"bug_id": [5], "text": "hi"})
    good = _row(
        1,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"a": 1}},
    )
    rows = [(bad, []), (good, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([bad, good]), _FakeRun(status=RunStatus.succeeded.value), rows
    )

    # Both were dispatched individually rather than coalesced, and nothing raised.
    assert len(handler.calls) == 2


async def test_a_row_that_cannot_be_merged_fails_by_itself(monkeypatch):
    # `merge_resolved` folds a same-bug comment into the update's body, so a non-dict
    # `changes` raises there — outside `_dispatch`'s guard, as an argument to it.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result=None, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    update = _row(
        0,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": "oops"},
    )
    comment = _row(1, "pending", params={"bug_id": 5, "text": "hi"})
    rows = [(update, []), (comment, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([update, comment]), _FakeRun(status=RunStatus.succeeded.value), rows
    )

    assert handler.calls == []  # nothing was sent
    assert update.status == "failed" and update.error
    assert comment.status == "failed"  # coalesced with it, so it shares the outcome


async def test_a_non_list_actions_summary_records_nothing(monkeypatch):
    # `enumerate(5)` would raise straight out of the route.
    run = _FakeRun(status=RunStatus.succeeded.value, summary={"actions": 5})
    assert await actions_applier.ensure_action_rows(_FakeDB(), run) == []


async def test_a_row_an_interrupted_apply_left_behind_is_retryable(monkeypatch):
    # Holding the lock rather than marking the row is what makes a crash self-healing:
    # the transaction rolls back, so the row is still `pending` and nothing has to
    # notice it was ever in flight. A `failed` row is retried the same way.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result=None, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    for status in ("pending", "failed"):
        row = _row(0, status)
        await actions_applier._apply_pending_rows(
            _FakeDB([row]), _FakeRun(status=RunStatus.succeeded.value), [(row, [])]
        )
        assert row.status == "applied", status


async def test_a_coalesced_group_is_locked_as_one(monkeypatch):
    # The group becomes a single Bugzilla PUT, so acting on part of it would mean a
    # partial post. Either the whole group is ours or none of it is.
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result=None, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    update = _row(
        0,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"a": 1}},
    )
    comment = _row(1, "pending", params={"bug_id": 5, "text": "hi"})
    rows = [(update, []), (comment, [])]
    db = _FakeDB([update, comment])
    # One member of the group was already applied elsewhere.
    db.set_db_status(comment.id, "applied")

    await actions_applier._apply_pending_rows(
        db, _FakeRun(status=RunStatus.succeeded.value), rows
    )

    assert handler.calls == []
    # The member we could have claimed is left alone rather than half-applied.
    assert update.status == "pending"


# --- coalescing same-bug Bugzilla mutations into one PUT ---------------- #


async def test_coalesces_update_and_comment_into_one_put(monkeypatch):
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"bug_id": 5}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    update = _row(
        0,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"status": "RESOLVED"}},
    )
    other = _row(
        1,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 99, "text": "different bug"},
    )
    comment = _row(
        2,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "done"},
    )
    rows = [(update, []), (other, []), (comment, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]),
        _FakeRun(status=RunStatus.succeeded.value),
        rows,
    )

    # Two calls: the standalone comment on bug 99, then ONE combined PUT for
    # bug 5 (applied at the group's max idx) carrying field change + comment.
    assert handler.calls == [
        {"bug_id": 99, "text": "different bug"},
        {
            "bug_id": 5,
            "changes": {"status": "RESOLVED"},
            "comment": {"body": "done", "is_private": False},
        },
    ]
    assert update.status == "applied" and comment.status == "applied"
    assert update.result == {"bug_id": 5} and comment.result == {"bug_id": 5}


async def test_extra_comments_applied_separately(monkeypatch):
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    update = _row(
        0,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"status": "RESOLVED"}},
    )
    near = _row(
        1,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "near"},
    )
    far = _row(
        2,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "far"},
    )
    rows = [(update, []), (near, []), (far, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]),
        _FakeRun(status=RunStatus.succeeded.value),
        rows,
    )

    # Field change rides with the closest comment ("near"); "far" is its own PUT.
    assert handler.calls == [
        {
            "bug_id": 5,
            "changes": {"status": "RESOLVED"},
            "comment": {"body": "near", "is_private": False},
        },
        {"bug_id": 5, "text": "far"},
    ]


async def test_lone_same_type_actions_on_different_bugs_not_merged(monkeypatch):
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    u5 = _row(
        0,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"a": 1}},
    )
    u6 = _row(
        1,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 6, "changes": {"b": 2}},
    )
    rows = [(u5, []), (u6, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]),
        _FakeRun(status=RunStatus.succeeded.value),
        rows,
    )
    # Different bugs, one update each -> no coalescing, two raw PUTs.
    assert handler.calls == [
        {"bug_id": 5, "changes": {"a": 1}},
        {"bug_id": 6, "changes": {"b": 2}},
    ]


async def test_coalesced_group_failure_marks_all_then_retries(monkeypatch):
    failing = _RecordingHandler(
        SimpleNamespace(status="failed", result=None, error="boom")
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: failing)

    update = _row(
        0,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"a": 1}},
    )
    comment = _row(
        1,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "c"},
    )
    done = _row(
        2,
        "applied",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "already"},
        result={"x": 1},
        applied_at="then",
    )
    rows = [(update, []), (comment, []), (done, [])]
    run = _FakeRun(status=RunStatus.succeeded.value)

    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]), run, rows
    )
    # One combined call; both members failed; the already-applied row untouched.
    assert len(failing.calls) == 1
    assert update.status == "failed" and comment.status == "failed"
    assert done.status == "applied" and done.result == {"x": 1}

    # Retry: only the still-failed members re-group; the applied one is skipped.
    ok = _RecordingHandler(
        SimpleNamespace(status="applied", result={"bug_id": 5}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: ok)
    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]), run, rows
    )
    assert len(ok.calls) == 1
    assert update.status == "applied" and comment.status == "applied"


async def test_backward_placeholder_resolves_in_coalesced_comment(monkeypatch):
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"url": "http://x/D1"}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: handler)

    patch = _row(
        0, "pending", action_type="phabricator.submit_patch", params={}, ref="patch"
    )
    update = _row(
        1,
        "pending",
        action_type="bugzilla.update_bug",
        params={"bug_id": 5, "changes": {"a": 1}},
    )
    comment = _row(
        2,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "see {{actions.patch.url}}"},
    )
    rows = [(patch, []), (update, []), (comment, [])]

    await actions_applier._apply_pending_rows(
        _FakeDB([row for row, _ in rows]),
        _FakeRun(status=RunStatus.succeeded.value),
        rows,
    )

    # The patch applies first (its own idx), seeding results_by_ref; the
    # coalesced comment then resolves {{actions.patch.url}} at the group anchor.
    assert handler.calls == [
        {},
        {
            "bug_id": 5,
            "changes": {"a": 1},
            "comment": {"body": "see http://x/D1", "is_private": False},
        },
    ]
