"""Read the actions a run recorded: the bug updates, the comments and the needinfos."""

from typing import NamedTuple

UPDATE_BUG = "bugzilla.update_bug"
ADD_COMMENT = "bugzilla.add_comment"


class Update(NamedTuple):
    """One recorded ``bugzilla.update_bug``. ``changes`` is {} when it is not an object."""

    action_id: str
    bug_id: object
    changes: dict


def action_params(action: dict) -> dict:
    """The action's params, or {} when they are not an object."""
    params = action.get("params")
    return params if isinstance(params, dict) else {}


def is_bug_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def updates(actions: list[dict], bug: object = None) -> list[Update]:
    """The recorded bug updates, in order. With ``bug``, only the updates on that bug."""
    found = []
    for action in actions:
        if action.get("type") != UPDATE_BUG:
            continue
        params = action_params(action)
        if bug is not None and params.get("bug_id") != bug:
            continue
        changes = params.get("changes")
        found.append(
            Update(
                action.get("action_id", ""),
                params.get("bug_id"),
                changes if isinstance(changes, dict) else {},
            )
        )
    return found


def comment_texts(actions: list[dict]) -> list[str]:
    """The text of each recorded comment."""
    texts = (
        action_params(a).get("text") for a in actions if a.get("type") == ADD_COMMENT
    )
    return [t for t in texts if isinstance(t, str)]


def needinfo_requestees(changes: dict) -> list[object]:
    """The requestee of each flag in one update's ``changes``.

    The hooks let a run set needinfo flags only.
    """
    flags = changes.get("flags") or ()
    return [f.get("requestee", "") for f in flags if isinstance(f, dict)]
