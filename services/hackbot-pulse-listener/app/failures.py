"""Extract the failing test groups from a Taskcluster test task, via mozci.

A ``task-failed`` pulse event names a failing test *task* but not which test
manifests (groups) actually failed. Rather than fetch and parse artifacts
ourselves, we ask mozci: its errorsummary data source reads the task's structured
results and returns the failing groups with their failing tests and failure
types. This works across harnesses (mochitest / xpcshell / web-platform-tests),
keyed only by task id, and stays in sync with CI format changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import mozci.push  # noqa: F401  (imported so mozci registers its data sources)
from mozci.task import TestTask, is_bad_group, wpt_workaround

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailingGroup:
    """A failing test manifest and a representative failing test within it."""

    group: str
    test: str
    failure_type: str


def _failure_types(task: TestTask) -> dict:
    """mozci: ``{group: [(test_name, FailureType), ...]}`` for a task."""
    return task.failure_types


def _canonical_group(task: TestTask, group: str) -> str:
    """Rewrite a group name the way mozci does.

    The errorsummary names web-platform-tests groups by URL path ("/html/foo.html")
    while mozci keys the results we compare against by source path
    ("testing/web-platform/tests/html/foo.html"), so the regression check only ever
    matches if we apply the same transform.
    """
    if task.is_wpt and group.startswith((":/", "/")):
        return wpt_workaround(group)
    return group


def failing_groups(task_id: str, label: str) -> list[FailingGroup]:
    """Failing test groups for a task, named the way mozci names them.

    Raises on any mozci/network error rather than returning nothing: an
    errorsummary that cannot be read must not be mistaken for "nothing failed",
    which would silently drop a real regression.
    """
    task = TestTask(id=task_id, label=label)
    groups: list[FailingGroup] = []
    for group, fails in _failure_types(task).items():
        if not group or not fails or group == "/":
            continue
        canonical = _canonical_group(task, group)
        # mozci drops these from its own results, so they could never match.
        if is_bad_group(task_id, canonical):
            continue
        test, ftype = fails[0]
        groups.append(
            FailingGroup(
                group=canonical,
                test=test,
                failure_type=getattr(ftype, "name", str(ftype)),
            )
        )
    return groups
