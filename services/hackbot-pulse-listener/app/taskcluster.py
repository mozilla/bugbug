import logging

import taskcluster

from app.config import settings

logger = logging.getLogger(__name__)

_queue: taskcluster.Queue | None = None


def _get_queue() -> taskcluster.Queue:
    global _queue
    if _queue is None:
        _queue = taskcluster.Queue({"rootUrl": settings.taskcluster_root_url})
    return _queue


def get_task(task_id: str) -> dict:
    """Fetch a full task definition. Definitions are public, so no credentials."""
    return _get_queue().task(task_id)


def get_hg_revision(task: dict) -> str | None:
    """Return the GECKO_HEAD_REV (Mercurial revision) of a task, or None.

    The revision is not in the pulse message, hence the task definition fetch.
    GECKO_HEAD_REV is an hg revision; the build-repair agent needs a git SHA,
    so callers must convert it (see app.lando.hg_to_git).
    """
    return task.get("payload", {}).get("env", {}).get("GECKO_HEAD_REV")


def is_action_scheduled(task: dict) -> bool:
    """True if an action task -- a backfill or retrigger -- created this task.

    Taskgraph sets ``extra.parent`` to the task that scheduled this one: the
    push's decision task, which is also the task group id, for everything the
    push itself scheduled, and the action-callback task for anything added
    afterwards. A backfill therefore points at an action task while its group
    still points at the original push's decision task. A missing ``extra.parent``
    fails open (reported as not action-scheduled) so an unexpected task shape
    never drops a real failure.
    """
    parent = (task.get("extra") or {}).get("parent")
    if not parent:
        return False
    return parent != task.get("taskGroupId")
