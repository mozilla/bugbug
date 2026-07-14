import logging
import time

from mozci.errors import ParentPushNotFound
from mozci.push import Push

logger = logging.getLogger(__name__)

# Maximum number of ancestor pushes to walk back over coalescing gaps before
# giving up. Mirrors mozci's own MAX_DEPTH.
MAX_DEPTH = 20

# When the nearest ancestor that ran the build has not settled yet (still
# running/pending, or its result has not propagated into mozci's data sources),
# retry the whole check in-process after a delay instead of racing ahead. On
# autoland a stack lands and its pushes build near-simultaneously, so a parent
# build of the same label often resolves seconds to a couple of minutes after
# the tip's; skipping it as if coalesced misreports an inherited failure as new.
# The budget is small so the (single) consumer thread is never blocked for long.
RETRY_DELAY_SECONDS = 60
MAX_RETRIES = 3

# mozci results vary by data source (Taskcluster vs Treeherder), so match both
# vocabularies. Only genuine build failures count as failed; infra results
# ("exception", "canceled", "superseded", ...) are deliberately non-decisive so
# they never suppress a real regression.
_PASSED_RESULTS = ("passed", "success")
_FAILED_RESULTS = ("busted", "failed")

# States in which a task will no longer change (mirrors mozci's TASK_FINAL_STATES).
# A build in any other state (running, pending, unscheduled) is still in flight,
# so its result is not knowable yet and the check should wait for it.
_TERMINAL_STATES = ("completed", "failed", "exception")

# Sentinel meaning the decision cannot be made yet because an ancestor build is
# still in flight; the caller retries after a delay.
_PENDING = object()


def _build_status(push: Push, label: str):
    """Return 'passed', 'failed', _PENDING, or None for a build label on a push.

    'passed'/'failed' come from a completed run with a decisive result.
    _PENDING means a task exists but has not settled yet (still running/pending,
    or its result has not been ingested), so waiting may change the answer.
    None means no decisive result and nothing in flight: the build was coalesced
    (never scheduled) or only hit infra errors (exception, ...), both of which
    are deliberately non-decisive so they never suppress a real regression.
    Retriggers are collapsed: any green run means 'passed'; only a genuine build
    failure counts as 'failed'.
    """
    label_tasks = [t for t in push.tasks if t.label == label]
    results = [t.result for t in label_tasks if t.state == "completed"]
    if any(r in _PASSED_RESULTS for r in results):
        return "passed"
    if any(r in _FAILED_RESULTS for r in results):
        return "failed"
    if any(t.state not in _TERMINAL_STATES for t in label_tasks):
        return _PENDING
    return None


def _classify(branch: str, rev: str, label: str):
    """Walk ancestors once. Returns True (new), False (inherited), or _PENDING.

    A fresh Push is built each call so retries re-fetch live data (recent pushes
    are not finalized in mozci, so their tasks are never served from cache).
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
    finds the nearest ancestor that did. When that ancestor's build is still in
    flight, retries in-process after a delay rather than racing ahead and
    misreporting an inherited failure as new. Fails open (returns True) on any
    mozci/network error, if the build stays unsettled after MAX_RETRIES, or if
    no ancestor within MAX_DEPTH ran the build, so we never silently drop a real
    regression.
    """
    try:
        for attempt in range(MAX_RETRIES + 1):
            result = _classify(branch, rev, label)
            if result is not _PENDING:
                return result
            if attempt < MAX_RETRIES:
                logger.info(
                    "Build %s unsettled on an ancestor of %s; retrying in %ss (%s/%s)",
                    label,
                    rev,
                    RETRY_DELAY_SECONDS,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(RETRY_DELAY_SECONDS)
    except Exception:
        logger.exception("Regression check failed for %s@%s; running agent", label, rev)
        return True

    logger.warning(
        "Build %s still unsettled after %s retries at %s; running agent",
        label,
        MAX_RETRIES,
        rev,
    )
    return True
