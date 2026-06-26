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


def get_revision(task_id: str) -> str | None:
    """Return the GECKO_HEAD_REV (git SHA) for a task, or None if unavailable.

    The revision is not in the pulse message, so we fetch the full task
    definition. Task definitions are public, so no credentials are needed.
    """
    task = _get_queue().task(task_id)
    return task.get("payload", {}).get("env", {}).get("GECKO_HEAD_REV")
