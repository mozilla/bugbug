"""Try-server-domain recordable actions."""

from __future__ import annotations

from typing import Annotated

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder

TRY_PUSH_ACTION_TYPE = "try_server.push"

# Anything gated on "this run pushes to try" — today the patch-series artifact
# built in ``context.publish_changes`` — keys off this set, for symmetry with
# ``phabricator.PATCH_ACTION_TYPES``.
TRY_ACTION_TYPES = frozenset({TRY_PUSH_ACTION_TYPE})


@tool
async def push(
    recorder: ActionsRecorder,
    tasks: Annotated[
        list[str],
        Field(
            description=(
                "Treeherder task labels to run, e.g. ['build-linux64/opt', "
                "'test-linux2404-64/opt-mochitest-browser-chrome-1']. Only the "
                "tasks that actually exercise your change: every extra label "
                "spends build machine time. Must not be empty — there is no "
                "'run everything' shorthand."
            )
        ),
    ],
    reasoning: Annotated[
        str, Field(description="Why you are pushing to try (for audit log).")
    ],
    title: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Single-line description of the push, used as the commit message "
                "shown on Treeherder (e.g. 'Bug 123 - verify the fix on Linux')."
            ),
        ),
    ] = None,
    ref: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional label for this action so a later action (e.g. a "
                "bugzilla.add_comment in the same run) can reference its "
                "result once applied, via {{actions.<ref>.url}} in that "
                "action's text."
            ),
        ),
    ] = None,
) -> str:
    """Run your changes on the Firefox try server.

    Use this to have CI verify a change you cannot verify locally — a platform
    you cannot build, or a test suite you cannot run. It does not deliver a fix:
    to submit code for review, use ``submit_patch`` (a try push and a revision
    are independent, so a run may reasonably do both).

    You do not supply a patch file, and you do not need to touch
    ``try_task_config.json``: your final code changes in the working directory
    are pushed as-is and the task selection is built from ``tasks``, so make and
    verify all your edits first, then call this once you are done. Calling it
    records the push as a proposed action for review; nothing is pushed during
    the run, so you will not see the results — a human reads them on Treeherder.

    Set `ref` if you want to reference the push's Treeherder URL from another
    action in the same run, written as `{{actions.<ref>.url}}` (for example,
    inside a bug comment).
    """
    cleaned = [task.strip() for task in tasks if task and task.strip()]
    if not cleaned:
        raise ToolError(
            "A try push needs at least one Treeherder task label in `tasks`; "
            "an empty selection would run nothing."
        )

    recorder.record(
        TRY_PUSH_ACTION_TYPE,
        {"tasks": cleaned, "title": title},
        reasoning=reasoning,
        ref=ref,
    )
    return f"Recorded {TRY_PUSH_ACTION_TYPE} (#{len(recorder.actions) - 1})."


TOOLS = tools_in(__name__)
