import logging
import time

from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push
from mozci.task import Status

logger = logging.getLogger(__name__)

# Poll an unsettled ancestor for up to MAX_WAIT_SECONDS before giving up.
POLL_INTERVAL_SECONDS = 120
MAX_WAIT_SECONDS = 60 * 60

# mozci spells a green result "passed" (Taskcluster) or "success" (Treeherder).
_PASSED_RESULTS = ("passed", "success")

# States/results whose outcome isn't knowable yet: still running, or awaiting an
# auto-retry after an infra exception.
_UNSETTLED_STATES = ("pending", "running", "exception")
_UNSETTLED_RESULTS = ("exception", "retry")

# The deciding ancestor hasn't settled yet; the caller waits and re-checks.
_PENDING = object()


def _build_status(push: Push, label: str):
    """'passed'/'failed'/_PENDING/None for a build label on a push.

    None is non-decisive (coalesced, never scheduled, or a non-pass/fail terminal
    state), so such gaps are skipped rather than mistaken for an inherited failure.
    """
    label_tasks = [t for t in push.tasks if t.label == label]
    if label_tasks:
        if any(t.result in _PASSED_RESULTS for t in label_tasks):
            return "passed"
        # Checked before failure: a still-running or auto-retried run may yet turn
        # green, and any green run wins, so wait rather than inherit prematurely.
        if any(
            t.state in _UNSETTLED_STATES or t.result in _UNSETTLED_RESULTS
            for t in label_tasks
        ):
            return _PENDING
        if any(t.failed for t in label_tasks):
            return "failed"
        return None

    # No task here: wait if the build was scheduled (result not visible yet),
    # skip if it was coalesced away.
    try:
        scheduled = label in push.scheduled_task_labels
    except Exception:
        logger.debug("Could not read scheduled task labels for %s", push.rev)
        scheduled = False
    return _PENDING if scheduled else None


def _classify(branch: str, rev: str, status_fn, describe: str):
    """Walk ancestors; return (state, last_green_rev).

    state is True (new), False (inherited) or _PENDING. status_fn(push) reports
    'passed'/'failed'/_PENDING/None for the failing unit (build label or test
    group). A fresh Push per call re-fetches live data for unfinalized pushes.
    """
    ancestor = Push(rev, branch=branch)
    for _ in range(MAX_DEPTH):
        try:
            ancestor = ancestor.parent
        except ParentPushNotFound:
            break
        status = status_fn(ancestor)
        if status is None:
            continue
        if status is _PENDING:
            logger.info(
                "%s not settled at %s; deferring for %s", describe, ancestor.rev, rev
            )
            return _PENDING, None
        if status == "failed":
            logger.info(
                "%s already failing at %s; inherited at %s", describe, ancestor.rev, rev
            )
            return False, None
        logger.info("%s passed at %s; new failure at %s", describe, ancestor.rev, rev)
        return True, ancestor.rev

    logger.warning(
        "No ancestor within %s pushes ran %s; running agent", MAX_DEPTH, describe
    )
    return True, None


def _await_new_failure(branch: str, rev: str, status_fn, describe: str):
    """(is_new, last_green_rev) for `rev`, waiting on an unsettled ancestor.

    Fails open ((True, None)) on any error, an ancestor unsettled past the
    deadline, or no deciding ancestor within MAX_DEPTH, so a real regression is
    never silently dropped.
    """
    try:
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while True:
            state, last_green = _classify(branch, rev, status_fn, describe)
            if state is not _PENDING:
                return state, last_green
            if time.monotonic() >= deadline:
                break
            logger.info(
                "Waiting %ss for an unsettled ancestor of %s (%s)",
                POLL_INTERVAL_SECONDS,
                describe,
                rev,
            )
            time.sleep(POLL_INTERVAL_SECONDS)
    except Exception:
        logger.exception(
            "Regression check failed for %s@%s; running agent", describe, rev
        )
        return True, None

    logger.warning(
        "%s still unsettled after %ss at %s; running agent",
        describe,
        MAX_WAIT_SECONDS,
        rev,
    )
    return True, None


def is_new_build_failure(branch: str, rev: str, label: str) -> bool:
    """True if this push introduced the build failure, False if it inherited it."""
    is_new, _ = _await_new_failure(
        branch, rev, lambda push: _build_status(push, label), f"build {label}"
    )
    return is_new


def _group_status(push: Push, group: str):
    """'passed'/'failed'/_PENDING/None for a test group (manifest) on a push.

    GroupSummary.status combines retriggers into PASS/FAIL/INTERMITTENT.
    INTERMITTENT and a missing group are non-decisive: the tests.firefox.dev
    flakiness rate judges intermittency instead.
    """
    summary = push.group_summaries.get(group)
    if summary is None:
        return None
    if push.is_group_running(summary):
        return _PENDING
    if summary.status == Status.PASS:
        return "passed"
    if summary.status == Status.FAIL:
        return "failed"
    return None


def is_new_test_failure(branch: str, rev: str, group: str) -> tuple[bool, str | None]:
    """(is_new, last_green_rev) for a failing test group; shares the build walk."""
    return _await_new_failure(
        branch, rev, lambda push: _group_status(push, group), f"group {group}"
    )
