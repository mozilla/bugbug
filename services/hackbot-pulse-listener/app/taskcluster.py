import json
import logging
import threading

import httpx
import taskcluster
from cachetools import TTLCache

from app import lando
from app.config import settings

logger = logging.getLogger(__name__)

_queue: taskcluster.Queue | None = None

# The artifacts of a finished task never change, so its results are held long.
_group_results_cache: TTLCache = TTLCache(maxsize=4096, ttl=6 * 60 * 60)
_cache_lock = threading.Lock()


def _get_queue() -> taskcluster.Queue:
    global _queue
    if _queue is None:
        _queue = taskcluster.Queue({"rootUrl": settings.taskcluster_root_url})
    return _queue


def get_task(task_id: str) -> dict:
    """Fetch a full task definition. Definitions are public, so no credentials."""
    return _get_queue().task(task_id)


def get_hg_revision(task: dict) -> str | None:
    """Return the Mercurial revision of a task's push, or None.

    The revision is not in the pulse message, hence the task definition fetch.
    Some tasks of an hg push check out the GitHub mirror, so their GECKO_HEAD_REV
    is a git SHA and is mapped back to hg. The build-repair agent needs a git SHA,
    so callers must convert the result (see app.lando.hg_to_git).
    """
    env = task.get("payload", {}).get("env", {})
    rev = env.get("GECKO_HEAD_REV")
    if rev and env.get("GECKO_HEAD_REPOSITORY", "").startswith("https://github.com/"):
        return lando.git_to_hg(rev)
    return rev


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


def group_results(task_id: str) -> dict[str, bool]:
    """``{group: passed}`` for a finished test task, from its errorsummary logs.

    Treeherder ingests the same results from these logs, but its group_results
    endpoint can lag behind or return nothing at all. A failure which passed on
    retry does not fail its group. Empty for a suite that reports no manifests, and
    for a task which ended before its harness wrote the results (crash, timeout).

    Note a group can be recorded as failing on a task whose job did not fail (wpt
    expected-fail metadata), so a failure is only meaningful for a task already known
    to have failed.
    """
    with _cache_lock:
        if task_id in _group_results_cache:
            return _group_results_cache[task_id]

    queue = _get_queue()
    results: dict[str, bool] = {}
    for artifact in queue.listLatestArtifacts(task_id)["artifacts"]:
        if not artifact["name"].endswith("errorsummary.log"):
            continue
        url = queue.buildUrl("getLatestArtifact", task_id, artifact["name"])
        response = httpx.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        for line in response.text.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("action") != "group_result" or data["status"] == "SKIP":
                continue
            # A group run by several harness invocations fails if any run failed.
            passed = data["status"] == "OK"
            results[data["group"]] = results.get(data["group"], True) and passed

    with _cache_lock:
        _group_results_cache[task_id] = results
    return results


class GroupResultsUnavailable(Exception):
    """A task records no per-manifest results."""


def failing_groups(task_id: str) -> list[str]:
    """The manifests this finished task reported as failing.

    An empty list means the task failed nothing at manifest level. Raises when the
    task has no results at all -- which is not the same thing, and must not be
    mistaken for it: a task-level failure (crash, timeout, harness error) would
    otherwise be silently dropped. Network errors propagate for the same reason.
    """
    results = group_results(task_id)
    if not results:
        raise GroupResultsUnavailable(f"Task {task_id} records no group results")
    return [
        group
        for group, passed in results.items()
        if not passed and group.strip() and group != "/"
    ]
