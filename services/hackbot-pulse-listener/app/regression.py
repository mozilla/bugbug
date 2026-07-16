import logging
import time

from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push

logger = logging.getLogger(__name__)


# When the nearest ancestor that ran the build has not produced a decisive
# result yet, wait for it in-process instead of racing ahead and misreporting an
# inherited failure as new. A build can take tens of minutes and may hit an
# infra exception and be auto-retried, so we poll until it settles or the
# deadline elapses (well above a normal build + one retry). Fails open after the
# deadline so a real regression is never silently dropped.
POLL_INTERVAL_SECONDS = 120
MAX_WAIT_SECONDS = 60 * 60

# A green build; mozci reports it as "passed" (Taskcluster) or "success"
# (Treeherder). Failures are read from mozci's Task.failed attribute instead of
# reimplementing the vocabulary.
_PASSED_RESULTS = ("passed", "success")

# A build in one of these states, or with one of these (infra) results, has not
# settled: it is still running or was retried after an exception, so its outcome
# is not knowable yet and we wait for it. Anything else that is not a decisive
# pass/fail (unscheduled, canceled, superseded, ...) or a build that never ran
# at all (coalesced) is treated as non-decisive and skipped.
_UNSETTLED_STATES = ("pending", "running", "exception")
_UNSETTLED_RESULTS = ("exception", "retry")

# Sentinel meaning the decision cannot be made yet because an ancestor build has
# not settled; the caller waits and re-checks.
_PENDING = object()


def _build_status(push: Push, label: str):
    """Return 'passed', 'failed', _PENDING, or None for a build label on a push.

    'passed'/'failed' come from a run with a decisive result. _PENDING means the
    result is not knowable yet: a task exists but has not settled (still running,
    or exceptioned and awaiting a retry), or no task is visible yet but the build
    was scheduled to run on this push (its result has not propagated). None means
    the build produced no decisive result and none is coming: it was coalesced /
    never scheduled, or it only reached a non-decisive terminal state. None is
    deliberately non-decisive so coalescing gaps are skipped and never suppress a
    real regression. Retriggers are collapsed: any green run means 'passed'; only
    a genuine build failure counts as 'failed'.
    """
    label_tasks = [t for t in push.tasks if t.label == label]
    if label_tasks:
        if any(t.result in _PASSED_RESULTS for t in label_tasks):
            return "passed"
        # Checked before failure: a retrigger that is still running or was
        # exceptioned and auto-retried may yet turn green, and any green run wins,
        # so we wait for it rather than prematurely inheriting a failure.
        if any(
            t.state in _UNSETTLED_STATES or t.result in _UNSETTLED_RESULTS
            for t in label_tasks
        ):
            return _PENDING
        # Not "not t.failed": a run can be neither passed nor failed (canceled,
        # superseded, ...). mozci's Task.failed also counts `exception`, but those
        # are unsettled and already returned above, so only genuine build failures
        # reach here.
        if any(t.failed for t in label_tasks):
            return "failed"
        # Ran but reached a non-decisive terminal state (canceled, ...): skip.
        return None

    # No task for this label on the push. Distinguish a build that was scheduled
    # to run here but whose result is not visible yet (wait) from one that was
    # never scheduled / coalesced away (skip), using the decision task's
    # scheduled set (available well before the builds themselves finish).
    try:
        scheduled = label in push.scheduled_task_labels
    except Exception:
        logger.debug("Could not read scheduled task labels for %s", push.rev)
        scheduled = False
    return _PENDING if scheduled else None


def _classify(branch: str, rev: str, label: str):
    """Walk ancestors once. Returns True (new), False (inherited), or _PENDING.

    A fresh Push is built each call so re-checks re-fetch live data (recent
    pushes are not finalized in mozci, so their tasks are never served from
    cache).
    """
    ancestor = Push(rev, branch=branch)
    for _ in range(MAX_DEPTH):
        try:
            ancestor = ancestor.parent
        except ParentPushNotFound:
            break
        status = _build_status(ancestor, label)
        if status is None:
            continue
        if status is _PENDING:
            logger.info(
                "Build %s not settled yet at %s; deferring decision for %s",
                label,
                ancestor.rev,
                rev,
            )
            return _PENDING
        if status == "failed":
            logger.info(
                "Build %s already failing at %s; inherited failure at %s",
                label,
                ancestor.rev,
                rev,
            )
            return False
        logger.info(
            "Build %s passed at %s; new failure introduced at %s",
            label,
            ancestor.rev,
            rev,
        )
        return True

    logger.warning(
        "No ancestor within %s pushes ran build %s; running agent", MAX_DEPTH, label
    )
    return True


def is_new_build_failure(branch: str, rev: str, label: str) -> bool:
    """Return True if this push introduced the failure, False if it inherited it.

    Walks back over pushes that did not run the build (coalescing) until it
    finds the nearest ancestor that did. When that ancestor's build has not
    settled yet, waits in-process and re-checks until it produces a decisive
    result or MAX_WAIT_SECONDS elapses, rather than racing ahead and
    misreporting an inherited failure as new. Fails open (returns True) on any
    mozci/network error, if the build stays unsettled past the deadline, or if
    no ancestor within MAX_DEPTH ran the build, so we never silently drop a real
    regression.
    """
    try:
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while True:
            result = _classify(branch, rev, label)
            if result is not _PENDING:
                return result
            if time.monotonic() >= deadline:
                break
            logger.info(
                "Waiting %ss for an unsettled ancestor build of %s (%s)",
                POLL_INTERVAL_SECONDS,
                rev,
                label,
            )
            time.sleep(POLL_INTERVAL_SECONDS)
    except Exception:
        logger.exception("Regression check failed for %s@%s; running agent", label, rev)
        return True

    logger.warning(
        "Build %s still unsettled after %ss at %s; running agent",
        label,
        MAX_WAIT_SECONDS,
        rev,
    )
    return True
