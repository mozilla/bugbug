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
from mozci import data

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailingGroup:
    """A failing test manifest and a representative failing test within it."""

    group: str
    test: str
    failure_type: str


def _failure_types(task_id: str) -> dict:
    """mozci: ``{group: [(test_name, FailureType), ...]}`` for a task."""
    return data.handler.get("test_task_failure_types", task_id=task_id)


def failing_groups(task_id: str) -> list[FailingGroup]:
    """Failing test groups for a task; empty on any error (gate fails open)."""
    try:
        by_group = _failure_types(task_id)
    except Exception:
        logger.exception("Could not read failing groups for task %s", task_id)
        return []

    groups: list[FailingGroup] = []
    for group, fails in by_group.items():
        if not group or not fails:
            continue
        test, ftype = fails[0]
        groups.append(
            FailingGroup(
                group=group, test=test, failure_type=getattr(ftype, "name", str(ftype))
            )
        )
    return groups
