import logging

from mozci.errors import ParentPushNotFound
from mozci.push import Push

logger = logging.getLogger(__name__)

# Maximum number of ancestor pushes to walk back over coalescing gaps before
# giving up. Mirrors mozci's own MAX_DEPTH.
MAX_DEPTH = 20

# mozci results vary by data source (Taskcluster vs Treeherder), so match both
# vocabularies. Only genuine build failures count as failed; infra results
# ("exception", "canceled", "superseded", ...) are deliberately non-decisive so
# they never suppress a real regression.
_PASSED_RESULTS = ("passed", "success")
_FAILED_RESULTS = ("busted", "failed")


def _build_status(push: Push, label: str) -> str | None:
    """Return 'passed', 'failed', or None for a build label on a push.

    None means the build produced no decisive result here: it was coalesced (not
    scheduled), is still running, or only hit infra errors. Retriggers are
    collapsed: any green run means 'passed'; only a genuine build failure
    (never an infra exception) counts as 'failed'.
    """
    results = [
        t.result for t in push.tasks if t.label == label and t.state == "completed"
    ]
    if any(r in _PASSED_RESULTS for r in results):
        return "passed"
    if any(r in _FAILED_RESULTS for r in results):
        return "failed"
    return None


def is_new_build_failure(branch: str, rev: str, label: str) -> bool:
    """Return True if this push introduced the failure, False if it inherited it.

    Walks back over pushes that did not run the build (coalescing) until it
    finds the nearest ancestor that did. Fails open (returns True) on any
    mozci/network error or if no ancestor within MAX_DEPTH ran the build, so we
    never silently drop a real regression.
    """
    try:
        ancestor = Push(rev, branch=branch)
        for _ in range(MAX_DEPTH):
            try:
                ancestor = ancestor.parent
            except ParentPushNotFound:
                break
            status = _build_status(ancestor, label)
            if status is None:
                continue
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
    except Exception:
        logger.exception("Regression check failed for %s@%s; running agent", label, rev)
        return True

    logger.warning(
        "No ancestor within %s pushes ran build %s; running agent", MAX_DEPTH, label
    )
    return True
