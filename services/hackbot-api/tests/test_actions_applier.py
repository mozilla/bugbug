"""Tests for the action applier.

Covers the {{actions.<ref>.<field>}} placeholder resolver, the succeeded-run
gate + per-agent auto-apply opt-in and per-run consent in `on_run_completed`, and
the manual `apply_all_pending` path — see app/actions_applier.py.
"""

import logging
import uuid
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

import pytest
from app import actions_applier
from app.actions_applier import (
    apply_all_pending,
    on_run_completed,
    resolve_placeholders,
)
from app.agents import AGENT_REGISTRY
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
    inputs: dict | None = field(default_factory=dict)


def _spec(*, auto=True, consent=False):
    """A real `AgentSpec` built with `replace` off a registry entry.

    Every field then takes its production default, so a field added later can't read
    as absent. A hand-rolled namespace would instead raise `AttributeError` inside
    `_auto_apply_blocker`.
    """
    return replace(
        AGENT_REGISTRY["bug-fix"],
        auto_apply_actions=auto,
        auto_apply_requires_consent=consent,
    )


def _auto_applies(spec, run):
    return actions_applier._auto_apply_blocker(spec, run) is None


def _run_with_findings(**findings):
    return _FakeRun(
        status=RunStatus.succeeded.value,
        inputs={"bug_id": 2014702},
        summary={"findings": findings, "actions": []},
    )


def test_auto_apply_off_never_applies():
    # `auto_apply_actions` is checked first, so what a run reports about itself makes
    # no difference when the agent hasn't opted in at all.
    spec = _spec(auto=False, consent=True)
    assert not _auto_applies(spec, _run_with_findings(auto_apply=True))


def test_unknown_agent_never_applies():
    assert not _auto_applies(None, _run_with_findings(auto_apply=True))


def test_an_agent_that_needs_no_consent_applies_unconditionally():
    # Agents that opt in without asking for a per-run verdict keep applying whatever
    # findings say. `bug-fix` and `test-repair` predate this change.
    spec = _spec()
    assert _auto_applies(spec, _FakeRun(status=RunStatus.succeeded.value))
    assert _auto_applies(spec, _run_with_findings(auto_apply=False))


# --- the run's own verdict ----------------------------------------------- #
#
# The agent decides `findings.auto_apply`, because `confidence` lands in its final
# message after every action is already recorded. What it may record at all is
# bounded on the agent side too, as it records — see `hooks.py` in frontend-triage.
# These tests cover only how the service honors the verdict.


def test_a_vouched_for_run_applies():
    spec = _spec(consent=True)
    assert _auto_applies(spec, _run_with_findings(auto_apply=True))


def test_a_run_that_did_not_vouch_for_itself_is_held():
    # The check is `is not True`, so anything short of a literal boolean true is held.
    # A run that reports no verdict has not given one.
    spec = _spec(consent=True)
    for findings in (
        {"auto_apply": False},
        {"auto_apply": None},
        {"auto_apply": "true"},  # a string is not consent
        {"auto_apply": 1},
        {},  # never reported
    ):
        assert not _auto_applies(spec, _run_with_findings(**findings)), findings


def test_a_run_with_no_findings_at_all_is_held():
    spec = _spec(consent=True)
    for summary in (None, {}, {"findings": None}, {"findings": {}}):
        run = _FakeRun(status=RunStatus.succeeded.value, summary=summary)
        assert not _auto_applies(spec, run), summary


def test_the_real_frontend_triage_spec_asks_for_consent():
    # Asserts the live registry entry rather than a paraphrase of it. Without
    # `auto_apply_requires_consent` the agent's verdict is ignored and a medium- or
    # low-confidence run posts the same as a high one.
    spec = AGENT_REGISTRY["frontend-triage"]
    assert spec.auto_apply_actions is True
    assert spec.auto_apply_requires_consent is True


def test_which_agents_auto_apply_without_asking_for_consent():
    # `bug-fix` and `test-repair` auto-apply whatever they record, and the apply step
    # dispatches against the runtime's *global* handler registry — creating bugs,
    # attaching files, submitting Phabricator patches. Both predate this change, and
    # bounding them is a decision about those agents, so this records the gap rather
    # than closing it. Failing here means a new agent opted in without bounding what
    # it records.
    unbounded = {
        name
        for name, spec in AGENT_REGISTRY.items()
        if spec.auto_apply_actions and not spec.auto_apply_requires_consent
    }
    assert unbounded == {"bug-fix", "test-repair"}


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def execute(self, *a, **k):
        raise AssertionError(
            "ensure_action_rows should be monkeypatched in these tests"
        )


def _patch_applier(monkeypatch, *, auto: bool | None, consent=False):
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
    registry = {} if auto is None else {"bug-fix": _spec(auto=auto, consent=consent)}
    monkeypatch.setattr(actions_applier, "AGENT_REGISTRY", registry)
    return calls


def test_successful_needinfo_run_appends_exact_flag_clear_action():
    agent_action = {
        "type": "bugzilla.add_comment",
        "params": {"bug_id": 5, "text": "done"},
    }
    run = _FakeRun(
        status=RunStatus.succeeded.value,
        summary={"actions": [agent_action]},
        inputs={"bug_id": 5, "bugzilla_needinfo_flag_id": 42},
    )

    assert actions_applier._actions_for_run(run) == [
        agent_action,
        {
            "type": "bugzilla.update_bug",
            "params": {
                "bug_id": 5,
                "changes": {"flags": [{"id": 42, "status": "X"}]},
            },
        },
    ]
    # The API-owned action must not alter the agent-authored summary.
    assert run.summary == {"actions": [agent_action]}


def test_needinfo_run_without_response_does_not_append_clear_action():
    run = _FakeRun(
        status=RunStatus.succeeded.value,
        summary={"actions": []},
        inputs={"bug_id": 5, "bugzilla_needinfo_flag_id": 42},
    )
    assert actions_applier._actions_for_run(run) == []


@pytest.mark.parametrize(
    "inputs",
    [
        pytest.param({"bug_id": 5}, id="no-needinfo-key"),
        pytest.param({"bug_id": 5, "bugzilla_needinfo_flag_id": None}, id="null-flag"),
        pytest.param(None, id="no-inputs"),
    ],
)
def test_run_without_needinfo_flag_is_left_untouched(inputs):
    """A plain bug-fix run must pass through unchanged, not error."""
    agent_action = {
        "type": "bugzilla.add_comment",
        "params": {"bug_id": 5, "text": "done"},
    }
    run = _FakeRun(
        status=RunStatus.succeeded.value,
        summary={"actions": [agent_action]},
        inputs=inputs,
    )
    assert actions_applier._actions_for_run(run) == [agent_action]


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


async def test_succeeded_vouched_for_run_applies(monkeypatch):
    calls = _patch_applier(monkeypatch, auto=True, consent=True)
    await on_run_completed(_FakeDB(), _run_with_findings(auto_apply=True))
    assert calls == {"ensured": True, "applied": True}


async def test_succeeded_unvouched_run_records_but_does_not_apply(monkeypatch):
    calls = _patch_applier(monkeypatch, auto=True, consent=True)
    # Recorded for the UI (and manual apply), but nothing reaches Bugzilla.
    await on_run_completed(_FakeDB(), _run_with_findings(auto_apply=False))
    assert calls == {"ensured": True, "applied": False}


async def test_other_agents_do_not_auto_apply():
    # Opting an agent in is a deliberate edit, so spell out who is in today:
    # bug-fix and test-repair auto-apply unconditionally, frontend-triage only when
    # the run vouched for itself, and everyone else stays human-gated.
    auto_apply = {n for n, s in AGENT_REGISTRY.items() if s.auto_apply_actions}
    assert auto_apply == {"bug-fix", "frontend-triage", "test-repair"}


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
        _FakeDB(), _FakeRun(status=RunStatus.succeeded.value), rows
    )

    # The already-applied row is untouched; its handler never runs.
    assert applied.status == "applied" and applied.result == {"pre": 1}
    # The failed and pending rows are both (re)applied, clearing the stale error.
    assert len(handler.calls) == 2
    assert failed.status == "applied" and failed.error is None
    assert pending.status == "applied"


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
        _FakeDB(), _FakeRun(status=RunStatus.succeeded.value), rows
    )

    # Two calls: the standalone comment on bug 99, then ONE combined PUT for
    # bug 5 (applied at the group's max idx) carrying field change + comment.
    assert handler.calls == [
        {"bug_id": 99, "text": "different bug"},
        {
            "bug_id": 5,
            "changes": {"status": "RESOLVED"},
            "comment": {"body": "done", "is_private": False, "is_markdown": True},
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
        _FakeDB(), _FakeRun(status=RunStatus.succeeded.value), rows
    )

    # Field change rides with the closest comment ("near"); "far" is its own PUT.
    assert handler.calls == [
        {
            "bug_id": 5,
            "changes": {"status": "RESOLVED"},
            "comment": {"body": "near", "is_private": False, "is_markdown": True},
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
        _FakeDB(), _FakeRun(status=RunStatus.succeeded.value), rows
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

    await actions_applier._apply_pending_rows(_FakeDB(), run, rows)
    # One combined call; both members failed; the already-applied row untouched.
    assert len(failing.calls) == 1
    assert update.status == "failed" and comment.status == "failed"
    assert done.status == "applied" and done.result == {"x": 1}

    # Retry: only the still-failed members re-group; the applied one is skipped.
    ok = _RecordingHandler(
        SimpleNamespace(status="applied", result={"bug_id": 5}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda t: ok)
    await actions_applier._apply_pending_rows(_FakeDB(), run, rows)
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
        _FakeDB(), _FakeRun(status=RunStatus.succeeded.value), rows
    )

    # The patch applies first (its own idx), seeding results_by_ref; the
    # coalesced comment then resolves {{actions.patch.url}} at the group anchor.
    assert handler.calls == [
        {},
        {
            "bug_id": 5,
            "changes": {"a": 1},
            "comment": {
                "body": "see http://x/D1",
                "is_private": False,
                "is_markdown": True,
            },
        },
    ]


async def test_comment_and_needinfo_clear_coalesce_into_one_update(monkeypatch):
    handler = _RecordingHandler(
        SimpleNamespace(status="applied", result={"needinfo_cleared": True}, error=None)
    )
    monkeypatch.setattr(actions_applier, "get_handler", lambda _type: handler)

    comment = _row(
        0,
        "pending",
        action_type="bugzilla.add_comment",
        params={"bug_id": 5, "text": "done"},
    )
    clear = _row(
        1,
        "pending",
        action_type="bugzilla.update_bug",
        params={
            "bug_id": 5,
            "changes": {"flags": [{"id": 42, "status": "X"}]},
        },
    )
    await actions_applier._apply_pending_rows(
        _FakeDB(),
        _FakeRun(status=RunStatus.succeeded.value),
        [(comment, []), (clear, [])],
    )

    # One PUT carrying both the reply and the flag retraction.
    assert handler.calls == [
        {
            "bug_id": 5,
            "changes": {"flags": [{"id": 42, "status": "X"}]},
            "comment": {"body": "done", "is_private": False, "is_markdown": True},
        }
    ]
    assert comment.status == "applied"
    assert clear.status == "applied"
