import logging
import time

from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push

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


def _label_tasks(push: Push, label: str) -> list:
    return [t for t in push.tasks if t.label == label]


def _unsettled(tasks: list) -> bool:
    """Whether any of these runs may still change outcome (running or auto-retried)."""
    return any(
        t.state in _UNSETTLED_STATES or t.result in _UNSETTLED_RESULTS for t in tasks
    )


def _was_scheduled(push: Push, label: str) -> bool:
    """Whether the push originally scheduled the label (results not visible yet)."""
    try:
        return label in push.scheduled_task_labels
    except Exception:
        logger.debug("Could not read scheduled task labels for %s", push.rev)
        return False


def _build_status(push: Push, label: str):
    """'passed'/'failed'/_PENDING/None for a build label on a push.

    None is non-decisive (coalesced, never scheduled, or a non-pass/fail terminal
    state), so such gaps are skipped rather than mistaken for an inherited failure.
    """
    tasks = _label_tasks(push, label)
    if tasks:
        if any(t.result in _PASSED_RESULTS for t in tasks):
            return "passed"
        # Checked before failure: a still-running or auto-retried run may yet turn
        # green, and any green run wins, so wait rather than inherit prematurely.
        if _unsettled(tasks):
            return _PENDING
        if any(t.failed for t in tasks):
            return "failed"
        return None

    # No task here: wait if the build was scheduled (result not visible yet),
    # skip if it was coalesced away.
    return _PENDING if _was_scheduled(push, label) else None


def _group_status(push: Push, group: str, label: str):
    """'passed'/'failed'/_PENDING/None for a test group on a push, for one label.

    Only tasks sharing the failing task's label are consulted. push.group_summaries
    folds every configuration together and reports FAIL when the manifest is broken
    on any platform, which would mask a genuine new failure on this one; a label
    pins the platform, suite, variant and chunk.

    The precedence matches _build_status: pass, then pending, then fail.
    """
    tasks = _label_tasks(push, label)
    results = [
        r
        for t in tasks
        for r in (getattr(t, "results", None) or [])
        if r.group == group
    ]
    if any(r.ok for r in results):
        return "passed"
    if _unsettled(tasks):
        return _PENDING
    if results:
        return "failed"

    # Nothing reported for the group: wait if the label was scheduled here, skip if
    # this push never ran it (coalesced, or the manifest was chunked elsewhere).
    return _PENDING if not tasks and _was_scheduled(push, label) else None


def _classify(head: Push, status_fn, describe: str):
    """Walk ancestors of `head`; True (new), False (inherited) or _PENDING.

    status_fn(push) reports 'passed'/'failed'/_PENDING/None for the failing unit
    (build label or test group).
    """
    ancestor = head
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
                "%s not settled at %s; deferring for %s",
                describe,
                ancestor.rev,
                head.rev,
            )
            return _PENDING
        if status == "failed":
            logger.info(
                "%s already failing at %s; inherited at %s",
                describe,
                ancestor.rev,
                head.rev,
            )
            return False
        logger.info(
            "%s passed at %s; new failure at %s", describe, ancestor.rev, head.rev
        )
        return True

    logger.warning(
        "No ancestor within %s pushes ran %s; running agent", MAX_DEPTH, describe
    )
    return True


def _await_new_failures(branch: str, rev: str, status_fn, units, describe: str) -> set:
    """The units whose failure `rev` introduced; the rest were inherited.

    status_fn(push, unit) reports 'passed'/'failed'/_PENDING/None. One walk serves
    every unit: mozci memoizes a push's task list per instance, so sharing the head
    push across units fetches each ancestor's (large) task data once instead of once
    per unit.

    Fails open -- undecided units counted as new -- on any error, an ancestor still
    unsettled past MAX_WAIT_SECONDS, or no deciding ancestor within MAX_DEPTH, so a
    real regression is never silently dropped.
    """
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    unresolved = list(units)
    new: set = set()
    while True:
        try:
            # A fresh head each attempt is what re-reads live data for a push whose
            # ancestors have not finished yet.
            head = Push(rev, branch=branch)
            pending = []
            for unit in unresolved:
                state = _classify(
                    head, lambda push, u=unit: status_fn(push, u), f"{describe} {unit}"
                )
                if state is _PENDING:
                    pending.append(unit)
                elif state:
                    new.add(unit)
        except Exception:
            logger.exception(
                "Regression check failed for %s@%s; running agent", describe, rev
            )
            return new | set(unresolved)

        if not pending:
            return new
        if time.monotonic() >= deadline:
            logger.warning(
                "%s still unsettled after %ss at %s; running agent",
                describe,
                MAX_WAIT_SECONDS,
                rev,
            )
            return new | set(pending)
        # _classify already logged which ancestor is unsettled; keep the per-poll
        # heartbeat at debug so a long wait isn't dozens of lines.
        logger.debug(
            "Waiting %ss for an unsettled ancestor of %s (%s)",
            POLL_INTERVAL_SECONDS,
            describe,
            rev,
        )
        time.sleep(POLL_INTERVAL_SECONDS)
        unresolved = pending


def is_new_build_failure(branch: str, rev: str, label: str) -> bool:
    """True if this push introduced the build failure, False if it inherited it."""
    return label in _await_new_failures(branch, rev, _build_status, [label], "build")


def new_test_failures(branch: str, rev: str, label: str, groups: list[str]) -> set[str]:
    """The failing groups this push introduced, for one task's label."""
    return _await_new_failures(
        branch,
        rev,
        lambda push, group: _group_status(push, group, label),
        groups,
        "group",
    )
