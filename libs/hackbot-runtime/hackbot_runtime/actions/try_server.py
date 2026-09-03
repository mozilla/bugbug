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


def validate_test_paths(tests: dict[str, list[str]]) -> dict[str, list[str]]:
    """Check the shape of an agent-supplied ``{suite: [paths]}`` narrowing."""
    cleaned: dict[str, list[str]] = {}
    for suite, paths in tests.items():
        name = suite.strip()
        if not name or "/" in name or name != "".join(name.split()):
            raise ToolError(
                f"{suite!r} is not a test suite name. The key is the suite whose "
                "harness runs the tests ('mochitest-browser-chrome', "
                "'xpcshell', 'web-platform-tests', ...), and the paths go in the "
                "value. Read both out of the try_task_config.json that "
                '`./mach try fuzzy --no-push -q "<query>" <paths>` prints, under '
                "`tasks` and `env.MOZHARNESS_TEST_PATHS`."
            )

        if not isinstance(paths, (list, tuple)):
            raise ToolError(
                f"The paths for suite {name!r} must be a list of "
                f"repository-relative paths, not a {type(paths).__name__}. This "
                "is the shape mach prints in MOZHARNESS_TEST_PATHS, so copy it "
                "from there as-is."
            )
        wanted = sorted({path.strip().strip("/") for path in paths if path.strip()})
        if not wanted:
            raise ToolError(
                f"No test paths given for suite {name!r}; drop the suite or give "
                "it at least one repository-relative path."
            )
        cleaned[name] = wanted
    return cleaned


@tool
async def push(
    recorder: ActionsRecorder,
    reasoning: Annotated[
        str, Field(description="Why you are pushing to try (for audit log).")
    ],
    tasks: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Treeherder task labels, e.g. ['build-linux64/opt']. Only tasks "
                "that exercise your change; each costs machine time. Not with "
                "`auto`."
            ),
        ),
    ] = None,
    auto: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "Let CI pick the tasks for the files you changed (`mach try "
                "auto`). Prefer this when unsure. Not with `tasks`."
            ),
        ),
    ] = False,
    tests: Annotated[
        dict[str, list[str]] | None,
        Field(
            default=None,
            description=(
                "Narrow the selection to specific tests: {suite: [repo-relative "
                "paths]}, e.g. {'mochitest-browser-chrome': "
                "['browser/base/content/test']}. Needs `tasks` or `auto` too. A "
                "suite name that is not the one running is silently ignored, so "
                "copy it from mach rather than guessing."
            ),
        ),
    ] = None,
    title: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "One-line commit message shown on Treeherder, e.g. 'Bug 123 - "
                "verify the fix on Linux'."
            ),
        ),
    ] = None,
    ref: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Label for this action so a later one can use "
                "{{actions.<ref>.url}} to link this push."
            ),
        ),
    ] = None,
) -> str:
    """Run your changes on the Firefox try server.

    For work only CI can verify — a platform you cannot build, a suite you cannot
    run. It does not deliver a fix; ``submit_patch`` does that.

    Pass `auto` (CI picks the tasks) or `tasks` (labels you name), plus optional
    `tests` to narrow to specific tests. Do not guess labels or suite names: run
    ``./mach try fuzzy --no-push -q "<query>" [paths]``, which pushes nothing,
    and copy `tasks` and `env.MOZHARNESS_TEST_PATHS` out of the config it prints.

    Your working-directory changes are pushed as-is, so finish your edits first.
    This records the push for review rather than performing it, so you will not
    see the results.

    Always set ``ref`` so a later action in the same run can link the new CI
    push's Treeherder URL as ``{{actions.<ref>.url}}``. Record this push before
    the action containing the link.
    """
    cleaned_tasks = [task.strip() for task in (tasks or []) if task and task.strip()]
    if cleaned_tasks and auto:
        raise ToolError(
            "Pass either `tasks` or `auto`, not both: `auto` lets CI choose the "
            "tasks, so naming them as well is contradictory."
        )
    if not cleaned_tasks and not auto:
        raise ToolError(
            "A try push needs a selection: pass `auto=True` to let CI pick the "
            "tasks for your change, or list Treeherder labels in `tasks`."
            + (
                " `tests` only narrows a selection down to those test paths; on "
                "its own it would run nothing."
                if tests
                else ""
            )
        )

    params: dict = {
        "tasks": cleaned_tasks or None,
        "auto": auto,
        "title": title,
    }
    if tests:
        params["test_paths"] = validate_test_paths(tests)

    recorder.record(TRY_PUSH_ACTION_TYPE, params, reasoning=reasoning, ref=ref)
    return f"Recorded {TRY_PUSH_ACTION_TYPE} (#{len(recorder.actions) - 1})."


TOOLS = tools_in(__name__)
