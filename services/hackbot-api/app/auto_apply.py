"""Per-agent limits on what a run may write to Bugzilla without a human.

An agent's `confidence` gates its *judgement*; a guard here gates its *reach*. The
two are separate because an action's params are model output and the apply step
dispatches them against the runtime's global handler registry — which can create
bugs, attach files and write to Phabricator — so restricting which tools the agent
was given does not restrict what its recorded actions can reach.
"""

from __future__ import annotations

from typing import Any

from app.database.models import Run, RunAction

_TRIAGE_FIELDS = frozenset({"keywords", "severity"})


def _is_bug_id(value: Any) -> bool:
    # An int or a plain run of digits, nothing looser: the handler interpolates this
    # raw value into the REST path, whereas `int()` would also accept `"2_014_702"`,
    # signs, whitespace and non-ASCII digits — validating a different string than the
    # one sent.
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, str) and value.isdigit())


def _field_change(field: str, value: Any) -> str | None:
    """Why setting `field` to `value` is more than an addition, or None."""
    if field == "severity":
        # A single-valued field has no additive form, so a scalar is the only way to
        # set it. The value isn't checked against Bugzilla's vocabulary: an unknown one
        # is rejected there, surfacing as a failed action rather than a wrong write.
        if isinstance(value, str) and value.strip():
            return None
        return f"severity is set to an unexpected {type(value).__name__}"

    # A bare list *replaces* every keyword already on the bug; `{"add": [...]}` is the
    # only form that adds.
    if not isinstance(value, dict):
        return f"{field} is set wholesale rather than added to"
    if set(value) - {"add"}:
        return f"{field} is edited with {', '.join(sorted(value))}, not add"
    additions = value.get("add")
    if not isinstance(additions, list) or not additions:
        return f"{field}'s add is not a non-empty list"
    if not all(isinstance(item, str) and item.strip() for item in additions):
        return f"{field} adds something that isn't a non-empty string"
    return None


def frontend_triage_guard(run: Run, rows: list[RunAction]) -> str | None:
    """Why this triage run needs a human, or None if it may apply unattended.

    What `rules/frontend-triage.md` sanctions: one plan comment and, at most, one
    obviously-correct field addition on the bug the run was asked about. A run
    proposing anything else is held whole rather than part-applied — the comment
    explains the field change and the two are coalesced into one Bugzilla PUT, so
    dropping one and applying the rest would post something the agent didn't propose.
    """
    expected_bug_id = (run.inputs or {}).get("bug_id")
    seen: set[str] = set()

    for row in rows:
        params = row.params or {}

        if row.type not in ("bugzilla.add_comment", "bugzilla.update_bug"):
            return f"{row.type} is not an action type it may apply unattended"
        if row.type in seen:
            return f"it records more than one {row.type}"
        seen.add(row.type)

        bug_id = params.get("bug_id")
        # Required, not merely compared when present: `bugzilla.create_bug` carries no
        # `bug_id` at all, and "no target" must not read as "target matches".
        if bug_id is None or expected_bug_id is None:
            return f"{row.type} names no bug to check against the run's input"
        if not _is_bug_id(bug_id):
            return f"it targets an unreadable bug id {bug_id!r}"
        if int(bug_id) != int(expected_bug_id):
            return f"it targets bug {bug_id}, not the run's bug {expected_bug_id}"

        # A private comment is invisible to the reporter and the public, which defeats
        # the review-by-visibility this design leans on. Wanting privacy is exactly the
        # case that wants a human.
        if row.type == "bugzilla.add_comment" and params.get("is_private"):
            return "it posts a private comment"

        if row.type != "bugzilla.update_bug":
            continue

        # `UpdateBugHandler` forwards a `comment` param straight into the PUT, so it is a
        # second route to posting one — including a private one, past the check above.
        # (A `comment` key inside `changes` is a third, caught by the allowlist below.)
        if params.get("comment") is not None:
            return "it carries its own comment rather than a coalesced one"
        # Not `or {}`: a falsey non-mapping (`[]`, `""`, `0`) would become an empty dict
        # and sail through as "changes nothing" instead of being held.
        changes = params.get("changes")
        if not isinstance(changes, dict) or not changes:
            return "its `changes` is not a non-empty mapping of fields"
        disallowed = sorted(set(changes) - _TRIAGE_FIELDS)
        if disallowed:
            return f"it changes {', '.join(disallowed)}, which it may not change"
        for field, value in changes.items():
            reason = _field_change(field, value)
            if reason is not None:
                return reason

    return None
