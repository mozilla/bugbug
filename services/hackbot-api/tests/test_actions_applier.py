"""Tests for the action applier.

Covers the {{actions.<ref>.<field>}} placeholder resolver, the succeeded-run
gate + per-agent auto-apply opt-in in `on_run_completed`, and the manual
`apply_all_pending` path — see app/actions_applier.py.
"""

import logging
import uuid
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from app import actions_applier
from app.actions_applier import (
    apply_all_pending,
    on_run_completed,
    resolve_placeholders,
)
from app.agents import AGENT_REGISTRY
from app.auto_apply import frontend_triage_guard
from app.schemas import Confidence, RunStatus


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


def _spec(*, auto=True, confidence=None, guard=None):
    """A real `AgentSpec`, not a stand-in.

    Built with `replace` off a registry entry so every field defaults to the production
    default and a newly-added one can't silently read as absent — a hand-rolled
    namespace would raise `AttributeError` deep inside the gate instead of exercising it.
    """
    return replace(
        AGENT_REGISTRY["bug-fix"],
        auto_apply_actions=auto,
        auto_apply_confidence=confidence,
        auto_apply_guard=guard,
    )


def _rows_for(run):
    """The `(row, attachments)` pairs `ensure_action_rows` would produce for `run`.

    The gate judges the persisted rows, since those are what gets dispatched.
    """
    return [
        (
            _row(
                idx,
                "pending",
                action_type=action.get("type"),
                params=action.get("params") or {},
            ),
            [],
        )
        for idx, action in enumerate((run.summary or {}).get("actions", []))
    ]


def _auto_applies(spec, run):
    return actions_applier._should_auto_apply(spec, run, _rows_for(run))


def _run_with_confidence(confidence):
    return _FakeRun(
        status=RunStatus.succeeded.value,
        summary={"findings": {"confidence": confidence}, "actions": []},
    )


def test_auto_apply_off_never_applies():
    # The master switch wins: confidence is irrelevant when the agent hasn't
    # opted in at all.
    spec = _spec(auto=False, confidence=frozenset({Confidence.high}))
    assert not _auto_applies(spec, _run_with_confidence("high"))


def test_unknown_agent_never_applies():
    assert not _auto_applies(None, _run_with_confidence("high"))


def test_no_confidence_restriction_applies_unconditionally():
    # Existing/future agents that opt in without naming confidence levels keep
    # applying regardless of what findings say.
    spec = _spec(confidence=None)
    assert _auto_applies(spec, _run_with_confidence("low"))
    assert _auto_applies(spec, _FakeRun(status=RunStatus.succeeded.value))


def test_confidence_restriction_admits_listed_level_only():
    spec = _spec(confidence=frozenset({Confidence.high}))
    assert _auto_applies(spec, _run_with_confidence("high"))
    assert not _auto_applies(spec, _run_with_confidence("medium"))
    assert not _auto_applies(spec, _run_with_confidence("low"))


def test_confidence_restriction_can_widen_to_several_levels():
    # Widening to medium later must be a config change, not a code change.
    spec = _spec(confidence=frozenset({Confidence.high, Confidence.medium}))
    assert _auto_applies(spec, _run_with_confidence("medium"))
    assert not _auto_applies(spec, _run_with_confidence("low"))


def test_confidence_restriction_is_case_and_space_insensitive():
    # `confidence` is parsed out of the model's free-form JSON block, so don't
    # let "High" silently mean "never apply".
    spec = _spec(confidence=frozenset({Confidence.high}))
    assert _auto_applies(spec, _run_with_confidence("High"))
    assert _auto_applies(spec, _run_with_confidence("  HIGH "))


def test_specs_name_confidence_levels_that_runs_can_actually_report():
    # The same trap on the config side: a spec naming a level no run can ever
    # report (`"High"`, `"very-high"`) would silently disable auto-apply. Both
    # sides now speak `Confidence`, so it can't be written down — this asserts the
    # registry actually uses it rather than raw strings that happen to match.
    for name, spec in AGENT_REGISTRY.items():
        if spec.auto_apply_confidence is None:
            continue
        assert all(
            isinstance(level, Confidence) for level in spec.auto_apply_confidence
        ), name


def test_missing_confidence_fails_closed():
    spec = _spec(confidence=frozenset({Confidence.high}))
    for run in (
        _FakeRun(status=RunStatus.succeeded.value),  # no summary at all
        _FakeRun(status=RunStatus.succeeded.value, summary={}),  # no findings
        _FakeRun(status=RunStatus.succeeded.value, summary={"findings": {}}),
        _run_with_confidence(None),
        _run_with_confidence(""),
        _run_with_confidence(42),  # not even a string
    ):
        assert not _auto_applies(spec, run)


# --- what an agent may do unattended ------------------------------------ #
#
# `confidence` gates the agent's judgement; these gate its reach. Action params
# are model output, and the agent spends the run reading bug comments nobody
# controls, so a high-confidence run is not by itself a licence to write anything
# anywhere.


TRIAGE_FIELDS = frozenset({"keywords", "severity"})


def _run_with_actions(*actions, confidence="high", bug_id=2014702, **findings):
    base = {"confidence": confidence, **findings}
    return _FakeRun(
        status=RunStatus.succeeded.value,
        inputs={"bug_id": bug_id},
        summary={"findings": base, "actions": list(actions)},
    )


def _comment(bug_id=2014702):
    return {"type": "bugzilla.add_comment", "params": {"bug_id": bug_id, "text": "hi"}}


def _update(changes, bug_id=2014702):
    return {
        "type": "bugzilla.update_bug",
        "params": {"bug_id": bug_id, "changes": changes},
    }


TRIAGE_TYPES = frozenset({"bugzilla.add_comment", "bugzilla.update_bug"})


def _triage_spec():
    # The real guard, so these exercise the shipped policy rather than a paraphrase.
    return _spec(confidence=frozenset({Confidence.high}), guard=frontend_triage_guard)


def test_actions_within_the_agents_authority_are_applied():
    run = _run_with_actions(_comment(), _update({"keywords": {"add": ["perf"]}}))
    assert _auto_applies(_triage_spec(), run)


def test_an_action_against_another_bug_holds_the_run():
    # The run was asked about one bug; it may not write to a different one.
    run = _run_with_actions(_comment(bug_id=999), bug_id=2014702)
    assert not _auto_applies(_triage_spec(), run)


def test_a_field_outside_the_allowlist_holds_the_run():
    # `bugzilla.update_bug` accepts any field the REST endpoint does. Triage
    # diagnoses; it does not resolve or reassign.
    for changes in (
        {"status": "RESOLVED"},
        {"resolution": "DUPLICATE"},
        {"assigned_to": "someone@mozilla.com"},
        {"component": "General"},
        {"keywords": {"add": ["perf"]}, "status": "RESOLVED"},  # one bad key is enough
    ):
        run = _run_with_actions(_update(changes))
        assert not _auto_applies(_triage_spec(), run), changes


def test_one_out_of_bounds_action_holds_the_whole_run():
    # All-or-nothing: the comment explains the field change and the two are
    # coalesced into a single Bugzilla PUT, so applying half would post something
    # the agent never proposed.
    run = _run_with_actions(_comment(), _update({"status": "RESOLVED"}))
    assert not _auto_applies(_triage_spec(), run)


def test_a_destructive_keyword_edit_holds_the_run():
    # The allowlist names `keywords`, but naming a field is not permitting every
    # edit to it. Bugzilla list fields take add/remove/set, and the recording tool
    # advertises all three to the model — so a name-only check would let a bug
    # comment talk the agent into wiping the bug's keywords unattended.
    for changes in (
        {"keywords": {"set": []}},  # wipes every keyword
        {"keywords": {"set": ["perf"]}},  # replaces rather than adds
        {"keywords": {"remove": ["regression"]}},
        {"keywords": {"add": ["perf"], "remove": ["regression"]}},
        {"keywords": {"add": "perf"}},  # not a list
    ):
        run = _run_with_actions(_update(changes))
        assert not _auto_applies(_triage_spec(), run), changes


def test_adding_a_keyword_is_still_allowed():
    run = _run_with_actions(_update({"keywords": {"add": ["perf"]}}))
    assert _auto_applies(_triage_spec(), run)


def test_a_nonsense_change_value_holds_the_run():
    for changes in (
        {"keywords": {"add": []}},  # adds nothing
        {"keywords": {"add": [""]}},  # blank keyword
        {"keywords": {"add": ["   "]}},
        {"keywords": {"add": [{"unexpected": "structure"}]}},
        {"keywords": {"add": ["perf", 7]}},
        {"severity": ""},  # blank
        {"severity": None},
        {"severity": True},  # a bool is not a severity
        {"severity": ["S2"]},
    ):
        run = _run_with_actions(_update(changes))
        assert not _auto_applies(_triage_spec(), run), changes


def test_changes_must_be_a_non_empty_mapping():
    # `params.get("changes") or {}` would turn the falsey ones into "changes nothing"
    # and wave them through instead of holding them.
    for changes in ([], "", 0, {}, "keywords=perf"):
        run = _run_with_actions(_update(changes))
        assert not _auto_applies(_triage_spec(), run), repr(changes)


def test_a_plain_field_value_is_still_allowed():
    run = _run_with_actions(_update({"severity": "S2"}))
    assert _auto_applies(_triage_spec(), run)


def test_a_private_comment_holds_the_run():
    # A private comment is invisible to the reporter and the public, so it defeats
    # the review-by-visibility the unattended path leans on, and hides what the bot
    # did. Wanting privacy is exactly the case that wants a human.
    action = _comment()
    action["params"]["is_private"] = True
    assert not _auto_applies(_triage_spec(), _run_with_actions(action))


def test_a_comment_smuggled_through_changes_holds_the_run():
    # `UpdateBugHandler` copies every `changes` key into the PUT body, so this is another
    # route to posting one. The field allowlist is what catches it.
    assert not _auto_applies(
        _triage_spec(), _run_with_actions(_update({"comment": "hi"}))
    )


def test_an_update_carrying_its_own_comment_holds_the_run():
    # `UpdateBugHandler` forwards a `comment` param straight into the PUT, so this is
    # a second route to posting one — including a private one, past the check above.
    # The coalescer synthesises that field from the sibling row, so a recorded one is
    # a payload nobody planned for.
    action = _update({"keywords": {"add": ["perf"]}})
    action["params"]["comment"] = {"body": "hi", "is_private": True}
    assert not _auto_applies(_triage_spec(), _run_with_actions(action))


def test_setting_a_list_field_wholesale_holds_the_run():
    # The bare-scalar form replaces the field. For `severity` that's the only way to
    # set it; for a list field like `keywords` it's the very replacement the add-only
    # rule exists to prevent, so the scalar form is allowed only where it's safe.
    assert not _auto_applies(
        _triage_spec(), _run_with_actions(_update({"keywords": "regression"}))
    )
    assert _auto_applies(_triage_spec(), _run_with_actions(_update({"severity": "S2"})))


def test_the_gate_judges_the_rows_that_will_be_applied(monkeypatch):
    # `ensure_action_rows` never rewrites an existing row, so a summary and its rows
    # can diverge. Approving the summary while a different payload goes to Bugzilla
    # would make the whole gate decorative.
    run = _run_with_actions(_comment())  # summary looks harmless
    rows = [
        (
            _row(
                0,
                "pending",
                action_type="bugzilla.update_bug",
                params={"bug_id": 2014702, "changes": {"status": "RESOLVED"}},
            ),
            [],
        )
    ]
    assert not actions_applier._should_auto_apply(_triage_spec(), run, rows)


def test_more_than_one_action_of_a_kind_holds_the_run():
    # An injected model told to write "a single brief comment" could record fifty.
    run = _run_with_actions(_comment(), _comment(), _comment())
    assert not _auto_applies(_triage_spec(), run)
    run = _run_with_actions(_update({"severity": "S2"}), _update({"severity": "S3"}))
    assert not _auto_applies(_triage_spec(), run)


def test_an_agent_with_no_guard_is_unrestricted():
    # Agents that predate the guard keep their existing reach.
    run = _run_with_actions(_update({"status": "RESOLVED"}))
    assert _auto_applies(_spec(confidence=frozenset({Confidence.high})), run)


def test_an_action_type_the_agent_may_not_take_holds_the_run():
    # The applier dispatches against the runtime's *global* handler registry, so
    # limiting the agent's tools does not limit what its persisted actions reach.
    for action_type in (
        "bugzilla.add_attachment",
        "phabricator.submit_patch",
        "phabricator.add_comment",
        "testrail.submit_test_plan",
    ):
        run = _run_with_actions(
            {"type": action_type, "params": {"bug_id": 2014702}},
        )
        assert not _auto_applies(_triage_spec(), run), action_type


def test_a_create_bug_action_holds_the_run():
    # `bugzilla.create_bug` has no `bug_id` at all, so a same-bug check that only
    # fires "when present" would wave it through and file a brand new bug.
    run = _run_with_actions({"type": "bugzilla.create_bug", "params": {"summary": "x"}})
    assert not _auto_applies(_triage_spec(), run)


def test_an_action_with_no_bug_id_holds_a_bug_scoped_run():
    run = _run_with_actions({"type": "bugzilla.add_comment", "params": {"text": "hi"}})
    assert not _auto_applies(_triage_spec(), run)


def test_a_bug_scoped_run_with_no_bug_input_is_held():
    # Nothing trustworthy to compare the action against.
    run = _FakeRun(
        status=RunStatus.succeeded.value,
        inputs={},
        summary={"findings": {"confidence": "high"}, "actions": [_comment()]},
    )
    assert not _auto_applies(_triage_spec(), run)


def test_the_real_frontend_triage_spec_is_guarded():
    # The live wiring, not a paraphrase of it: without this the whole guard is
    # unreachable and every rule above tests a spec nothing uses.
    assert AGENT_REGISTRY["frontend-triage"].auto_apply_guard is frontend_triage_guard


def test_only_bug_fix_still_auto_applies_without_a_guard():
    # A tripwire, and a record of a known gap rather than an endorsement of it.
    #
    # `bug-fix` auto-applies unguarded, so a succeeded run of it can dispatch anything
    # in the runtime's global handler registry — creating bugs, attaching files,
    # submitting Phabricator patches — against any target the model names. That
    # predates this change and bounding it is a product decision about that agent. This
    # assertion exists so the next agent to opt in has to guard itself or say why not.
    unguarded = {
        name
        for name, spec in AGENT_REGISTRY.items()
        if spec.auto_apply_actions and spec.auto_apply_guard is None
    }
    assert unguarded == {"bug-fix"}


def test_an_out_of_scope_run_is_held_even_at_high_confidence():
    # `rules/scoping.md` pairs out-of-scope with `actionable: false` *and*
    # `confidence: low`, but nothing makes the agent pair them.
    run = _run_with_actions(_comment(), actionable=False)
    assert not _auto_applies(_triage_spec(), run)
    # An agent that simply doesn't report `actionable` is not held back by it.
    assert _auto_applies(_triage_spec(), _run_with_actions(_comment()))
    assert _auto_applies(_triage_spec(), _run_with_actions(_comment(), actionable=True))


def test_a_bug_id_that_is_not_a_number_holds_the_run():
    run = _run_with_actions(_comment(bug_id="not-a-bug"))
    assert not _auto_applies(_triage_spec(), run)


def test_a_bug_id_given_as_a_string_still_matches():
    # JSON round-trips can stringify it; that's not an authority violation.
    run = _run_with_actions(_comment(bug_id="2014702"), bug_id=2014702)
    assert _auto_applies(_triage_spec(), run)


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def execute(self, *a, **k):
        raise AssertionError(
            "ensure_action_rows should be monkeypatched in these tests"
        )


def _patch_applier(monkeypatch, *, auto: bool | None, confidence=None):
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
        {} if auto is None else {"bug-fix": _spec(auto=auto, confidence=confidence)}
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


async def test_succeeded_high_confidence_run_applies(monkeypatch):
    calls = _patch_applier(
        monkeypatch, auto=True, confidence=frozenset({Confidence.high})
    )
    await on_run_completed(_FakeDB(), _run_with_confidence("high"))
    assert calls == {"ensured": True, "applied": True}


async def test_succeeded_low_confidence_run_records_but_does_not_apply(monkeypatch):
    calls = _patch_applier(
        monkeypatch, auto=True, confidence=frozenset({Confidence.high})
    )
    for level in ("medium", "low"):
        calls["ensured"] = calls["applied"] = False
        await on_run_completed(_FakeDB(), _run_with_confidence(level))
        # Recorded for the UI (and manual apply), but nothing reaches Bugzilla.
        assert calls == {"ensured": True, "applied": False}, level


async def test_frontend_triage_auto_applies_at_high_confidence_only():
    # Guards the live policy, not the mechanism: widening this set posts
    # lower-confidence analyses to real bugs, so it should be a deliberate edit.
    spec = AGENT_REGISTRY["frontend-triage"]
    assert spec.auto_apply_actions is True
    assert spec.auto_apply_confidence == frozenset({Confidence.high})


async def test_other_agents_do_not_auto_apply():
    # Opting an agent in is a deliberate edit, so spell out who is in today:
    # bug-fix auto-applies unconditionally, frontend-triage only at high
    # confidence, and everyone else stays human-gated.
    auto_apply = {n for n, s in AGENT_REGISTRY.items() if s.auto_apply_actions}
    assert auto_apply == {"bug-fix", "frontend-triage"}

    # frontend-triage is still the only agent with a confidence gate.
    gated = {n for n, s in AGENT_REGISTRY.items() if s.auto_apply_confidence}
    assert gated == {"frontend-triage"}


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
        _FakeDB(), _FakeRun(status=RunStatus.succeeded.value), rows
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
            "comment": {"body": "see http://x/D1", "is_private": False},
        },
    ]
