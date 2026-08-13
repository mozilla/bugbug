"""Record-time limits on what this agent may write to a bug.

Once a run marks itself high-confidence its actions are applied to Bugzilla with no
human in between, and an action's params are model output. These hooks bound that:
one public comment and one add-only ``keywords``/``severity`` change, both on the
bug being triaged.

They run at record time rather than at apply time for two reasons. The refusal
reaches the agent as a tool error it can correct in the same run, and the
out-of-bounds action never reaches ``summary.json``. ``ActionsRecorder`` runs hooks
before appending, so raising here aborts the recording (see
:data:`hackbot_runtime.actions.ActionHook`).

The action **type** needs no check. ``ENABLED_ACTION_TYPES`` filters the tools the
actions server exposes, so this agent has no way to record a
``bugzilla.create_bug`` or a Phabricator action in the first place.
"""

from __future__ import annotations

from typing import Any

from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionHook, ActionsRecorder

from .config import TRIAGE_FIELDS, TRIAGE_KEYWORDS, TRIAGE_SEVERITIES


def _check_only_one(recorder: ActionsRecorder, action_type: str) -> None:
    # The rules ask for a single comment and at most one field change, but nothing
    # else caps the count, and the agent reads every comment on the bug as untrusted
    # input. A run told to write "a single brief comment" could record fifty.
    if any(action["type"] == action_type for action in recorder.actions):
        raise ToolError(
            f"you have already recorded a {action_type}; record one per run, "
            "revising it rather than adding another"
        )


def _check_target_bug(params: dict, bug_id: int) -> None:
    if params.get("bug_id") != bug_id:
        raise ToolError(
            f"you are triaging bug {bug_id}; record actions against that bug, "
            f"not bug {params.get('bug_id')!r}"
        )


def _check_severity(value: Any) -> None:
    # Single-valued, so a scalar is the only way to set it — there is no additive
    # form to insist on the way there is for keywords. `isinstance` before the
    # membership test, because a list or dict is unhashable and `in` would raise
    # rather than reject.
    if not isinstance(value, str) or value not in TRIAGE_SEVERITIES:
        raise ToolError(
            f"severity {value!r} is not one you may set; "
            f"use one of {', '.join(sorted(TRIAGE_SEVERITIES))}"
        )


def _check_keywords(value: Any) -> None:
    # A bare list *replaces* every keyword already on the bug; `{"add": [...]}` is
    # the only form that adds one.
    if not isinstance(value, dict) or set(value) != {"add"}:
        raise ToolError(
            'keywords must be added, not set: pass {"add": ["…"]} rather than '
            f"{value!r}, which would replace the keywords already on the bug"
        )
    additions = value["add"]
    if not isinstance(additions, list) or not additions:
        raise ToolError("keywords' add must be a non-empty list")
    # `isinstance` first: an unhashable entry would make `in` raise rather than reject.
    unknown = [
        k for k in additions if not isinstance(k, str) or k not in TRIAGE_KEYWORDS
    ]
    if unknown:
        raise ToolError(
            f"keyword(s) {', '.join(repr(k) for k in unknown)} are not ones you may "
            f"add; use one of {', '.join(sorted(TRIAGE_KEYWORDS))}"
        )


def update_bug_hook(recorder: ActionsRecorder, bug_id: int) -> ActionHook:
    """Refuse a ``bugzilla.update_bug`` outside what this agent is trusted with.

    Mirrors what ``rules/frontend-triage.md`` and ``rules/severity-assessment.md``
    sanction: one add-only change to fields in :data:`~.config.TRIAGE_FIELDS`, with
    values from Bugzilla's own vocabulary, on ``bug_id`` — the bug the run was asked
    about.
    """

    def hook(action: dict) -> None:
        params = action.get("params") or {}
        _check_only_one(recorder, "bugzilla.update_bug")
        _check_target_bug(params, bug_id)

        changes = params.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ToolError("changes must be a non-empty mapping of field to value")

        disallowed = sorted(set(changes) - TRIAGE_FIELDS)
        if disallowed:
            raise ToolError(
                f"you may not change {', '.join(disallowed)}; this agent changes only "
                f"{', '.join(sorted(TRIAGE_FIELDS))}"
            )

        for field, value in changes.items():
            if field == "severity":
                _check_severity(value)
            else:
                _check_keywords(value)

    return hook


def add_comment_hook(recorder: ActionsRecorder, bug_id: int) -> ActionHook:
    """Refuse a ``bugzilla.add_comment`` this agent may not post.

    One public comment, on the bug being triaged. A private comment is invisible to
    the reporter and to everyone else on the bug, so nobody would see what an
    unattended run concluded — and the developers on the bug are the audience for it.
    """

    def hook(action: dict) -> None:
        params = action.get("params") or {}
        _check_only_one(recorder, "bugzilla.add_comment")
        _check_target_bug(params, bug_id)

        if params.get("is_private"):
            raise ToolError(
                "record the comment publicly: everyone on the bug needs to read it"
            )

    return hook
