"""Record-time limits on what this agent may write to a bug.

Once a run marks itself high-confidence its actions are applied to Bugzilla with no
human in between, and an action's params are model output. These hooks bound that:
one public comment on the bug being triaged, and nothing else.

They run at record time rather than at apply time for two reasons. The refusal
reaches the agent as a tool error it can correct in the same run, and the
out-of-bounds action never reaches ``summary.json``. ``ActionsRecorder`` runs hooks
before appending, so raising here aborts the recording (see
:data:`hackbot_runtime.actions.ActionHook`).

The action **type** needs no check, and there is no field-change hook.
``ENABLED_ACTION_TYPES`` filters the tools the actions server exposes, so this agent
has no way to record a ``bugzilla.update_bug``, a ``bugzilla.create_bug`` or a
Phabricator action in the first place -- ``severity`` is a suggestion in the comment
now, which is what left that tool with no caller.
"""

from __future__ import annotations

from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionHook, ActionsRecorder


def _check_no_comment_yet(recorder: ActionsRecorder) -> None:
    # The rules ask for a single comment, but nothing else caps the count, and the
    # agent reads every comment on the bug as untrusted input. A run told to write
    # "a single brief comment" could record fifty.
    if any(action["type"] == "bugzilla.add_comment" for action in recorder.actions):
        raise ToolError(
            "you have already recorded a comment; record one per run, "
            "revising it rather than adding another"
        )


def _check_target_bug(params: dict, bug_id: int) -> None:
    if params.get("bug_id") != bug_id:
        raise ToolError(
            f"you are triaging bug {bug_id}; record actions against that bug, "
            f"not bug {params.get('bug_id')!r}"
        )


def add_comment_hook(recorder: ActionsRecorder, bug_id: int) -> ActionHook:
    """Refuse a ``bugzilla.add_comment`` this agent may not post.

    One public comment, on the bug being triaged. A private comment is invisible to
    the reporter and to everyone else on the bug, so nobody would see what an
    unattended run concluded — and the developers on the bug are the audience for it.
    """

    def hook(action: dict) -> None:
        params = action.get("params") or {}
        _check_no_comment_yet(recorder)
        _check_target_bug(params, bug_id)

        if params.get("is_private"):
            raise ToolError(
                "record the comment publicly: everyone on the bug needs to read it"
            )

    return hook
