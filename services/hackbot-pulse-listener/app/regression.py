"""Whether a CI failure is new at a push or inherited from an ancestor.

The ancestor chain comes from mozci, which resolves it from the hg pushlog --
Treeherder itself uses mozci for exactly this. The per-push results come from
Treeherder, which keeps them small: mozci would instead pull the push's whole task
list from the Taskcluster queue, tens of megabytes per ancestor.

A build, and a test task from a suite that reports no manifests, are looked up by
task label. A test group is looked up by configuration (platform and build option)
instead, because a chunked test label carries its chunk number and chunk assignments
drift between pushes.
"""

import logging
import re
import time

from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push

from app import treeherder

logger = logging.getLogger(__name__)

# Poll an unsettled ancestor for up to MAX_WAIT_SECONDS before giving up and running
# the agent. An ancestor that has not settled within ten minutes is usually not about
# to: the walk either resolves in a few polls or the run it waits on is itself stuck,
# and holding the decision longer only delays the repair.
POLL_INTERVAL_SECONDS = 120
MAX_WAIT_SECONDS = 60 * 10

# Treeherder job results and states.
_PASSED_RESULTS = ("success",)
_FAILED_RESULTS = ("testfailed", "busted")
# Outcomes that aren't knowable yet: still queued or running, or awaiting an
# auto-retry after an infra exception.
_UNSETTLED_STATES = ("pending", "running")
_UNSETTLED_RESULTS = ("retry", "exception", "unknown")

# The deciding ancestor hasn't settled yet; the caller waits and re-checks.
_PENDING = object()


class WalkAborted(Exception):
    """The caller stopped needing this answer while the walk was running."""


def _unsettled(jobs: list[dict]) -> bool:
    """Whether any of these runs may still change outcome."""
    return any(
        job["state"] in _UNSETTLED_STATES or job["result"] in _UNSETTLED_RESULTS
        for job in jobs
    )


def _label_status(project: str, rev: str, label: str):
    """'passed'/'failed'/_PENDING/None for one task label on a push.

    None is non-decisive (never ran here, or a non-pass/fail terminal state), so such
    gaps are skipped rather than mistaken for an inherited failure.
    """
    jobs = treeherder.label_jobs(project, rev, label)
    if any(job["result"] in _PASSED_RESULTS for job in jobs):
        return "passed"
    # Checked before failure: a still-running or auto-retried run may yet turn green,
    # and any green run wins, so wait rather than inherit prematurely.
    if _unsettled(jobs):
        return _PENDING
    if any(job["result"] in _FAILED_RESULTS for job in jobs):
        return "failed"
    return None


def _group_status(project: str, rev: str, config: tuple[str, str], group: str):
    """'passed'/'failed'/_PENDING/None for a test group on a push, in one config.

    Only this configuration's runs are consulted, so a manifest already broken on
    another platform cannot mask a genuine new failure on this one.

    Precedence is pass, then fail, then pending -- unlike _label_status, which puts
    pending first. A recorded failure has to be decisive here because ``_unsettled``
    covers every job of the configuration, not just the ones that could run this
    manifest, and on a recent ancestor some unrelated job is nearly always still
    going: deferring on that spent the whole MAX_WAIT_SECONDS and then failed open.
    The cost is that an ancestor whose failure was itself intermittent now reads as
    inherited, so such a failure is skipped rather than investigated.
    """
    jobs = treeherder.config_jobs(project, rev, *config)
    results = treeherder.group_results(project, rev)
    recorded = [
        results[job["task_id"]][group]
        for job in jobs
        if group in (results.get(job["task_id"]) or {})
    ]
    if any(recorded):
        return "passed"
    if recorded:
        return "failed"
    if _unsettled(jobs):
        return _PENDING

    # Nothing recorded for the group: this push never ran it (coalesced, or the
    # manifest was chunked into another task).
    return None


def _classify(
    project: str,
    rev: str,
    status_fn,
    describe: str,
    first_pass=True,
    look_past_pending=False,
):
    """Walk ancestors of `rev`; True (new), False (inherited) or _PENDING.

    status_fn(ancestor_rev) reports 'passed'/'failed'/_PENDING/None for the failing
    unit (build label or test group). `first_pass` keeps the "not settled" notice to
    one line per unit rather than repeating it on every poll for up to an hour.

    With ``look_past_pending`` an unsettled ancestor is stepped over rather than
    waited for. An older ancestor that already failed still makes the failure
    inherited; one that passed makes it new somewhere between it and ``rev``, which
    is answer enough when the agent works out the culprit commit itself. On autoland
    the parent push is nearly always still running when a failure arrives, so
    waiting for it costs the whole MAX_WAIT_SECONDS on almost every real regression.
    """
    push = Push(rev, branch=project)
    notice = logger.info if first_pass else logger.debug
    pending_at = None
    for _ in range(MAX_DEPTH):
        try:
            push = push.parent
        except ParentPushNotFound:
            break
        status = status_fn(push.rev)
        if status is None:
            continue
        ancestor_link = treeherder.push_url(project, push.rev)
        if status is _PENDING:
            if not look_past_pending:
                notice(
                    "%s not settled at %s; deferring for %s -- %s",
                    describe,
                    push.rev,
                    rev,
                    ancestor_link,
                )
                return _PENDING
            if pending_at is None:
                pending_at = push.rev
                notice(
                    "%s not settled at %s; looking further back for %s -- %s",
                    describe,
                    push.rev,
                    rev,
                    ancestor_link,
                )
            continue
        if status == "failed":
            logger.info(
                "%s already failing at %s; inherited at %s -- %s",
                describe,
                push.rev,
                rev,
                ancestor_link,
            )
            return False
        if pending_at is None:
            logger.info(
                "%s passed at %s; new failure at %s -- %s",
                describe,
                push.rev,
                rev,
                ancestor_link,
            )
        else:
            logger.info(
                "%s passed at %s and not yet settled at %s; new failure at %s or "
                "just before it -- %s",
                describe,
                push.rev,
                pending_at,
                rev,
                ancestor_link,
            )
        return True

    if pending_at is not None:
        notice(
            "%s not settled at %s and undecided further back; deferring for %s -- %s",
            describe,
            pending_at,
            rev,
            treeherder.push_url(project, pending_at),
        )
        return _PENDING
    logger.warning(
        "No ancestor within %s pushes ran %s; running agent -- %s",
        MAX_DEPTH,
        describe,
        treeherder.push_url(project, rev),
    )
    return True


def _await_new_failures(
    project: str,
    rev: str,
    status_fn,
    units,
    describe: str,
    should_abort=None,
    look_past_pending=False,
) -> set:
    """The units whose failure `rev` introduced; the rest were inherited.

    status_fn(ancestor_rev, unit) reports 'passed'/'failed'/_PENDING/None.

    Fails open -- undecided units counted as new -- on any error, an ancestor still
    unsettled past MAX_WAIT_SECONDS, or no deciding ancestor within MAX_DEPTH, so a
    real regression is never silently dropped.

    ``should_abort`` is polled between attempts; once it returns True the walk raises
    WalkAborted, because a wait of up to MAX_WAIT_SECONDS is not worth spending on an
    answer the caller has stopped being able to act on.
    """
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    unresolved = list(units)
    new: set = set()
    first_pass = True
    while True:
        # Checked outside the try below, which would otherwise swallow this and
        # fail open.
        if should_abort is not None and should_abort():
            raise WalkAborted(f"{describe} at {rev}")
        try:
            pending = []
            for unit in unresolved:
                state = _classify(
                    project,
                    rev,
                    lambda ancestor, u=unit: status_fn(ancestor, u),
                    f"{describe} {unit}",
                    first_pass=first_pass,
                    look_past_pending=look_past_pending,
                )
                if state is _PENDING:
                    pending.append(unit)
                elif state:
                    new.add(unit)
        except Exception:
            logger.exception(
                "Regression check failed for %s@%s; running agent -- %s",
                describe,
                rev,
                treeherder.push_url(project, rev),
            )
            return new | set(unresolved)

        if not pending:
            return new
        if time.monotonic() >= deadline:
            logger.warning(
                "%s still unsettled after %ss at %s; running agent -- %s",
                describe,
                MAX_WAIT_SECONDS,
                rev,
                treeherder.push_url(project, rev),
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
        first_pass = False


def is_stale_push(project: str, rev: str, max_age_seconds: float) -> bool:
    """Whether the push landed more than ``max_age_seconds`` ago.

    A task can fail long after its push -- a backfill scheduled weeks later, a task
    that sat queued, a listener restart replaying an old message -- and repairing a
    push that has long since been superseded helps nobody. Fails open (returns False)
    when the push date cannot be read, so a real regression is never silently dropped.
    """
    try:
        # Push.date is in seconds since the epoch, despite what mozci's docstring
        # says: it is hgmo's `pushdate[0]`, which is a Unix timestamp.
        age = time.time() - Push(rev, branch=project).date
    except Exception:
        logger.exception(
            "Could not read the push date for %s@%s; running agent -- %s",
            project,
            rev,
            treeherder.push_url(project, rev),
        )
        return False

    if age > max_age_seconds:
        logger.info(
            "Push %s@%s landed %.1fh ago (limit %.1fh); skipping -- %s",
            project,
            rev,
            age / 3600,
            max_age_seconds / 3600,
            treeherder.push_url(project, rev),
        )
        return True
    return False


# A trailing chunk number, e.g. "-12" on mochitest-browser-chrome-12.
_CHUNKED_LABEL = re.compile(r"-\d+$")


def unchunked(label: str) -> str:
    """The task label without its chunk number, the same on every push."""
    return _CHUNKED_LABEL.sub("", label)


def _is_new_label_failure(
    project: str,
    rev: str,
    label: str,
    describe: str,
    should_abort=None,
    look_past_pending=False,
) -> bool:
    return label in _await_new_failures(
        project,
        rev,
        lambda ancestor, unit: _label_status(project, ancestor, unit),
        [label],
        describe,
        should_abort,
        look_past_pending,
    )


def is_new_build_failure(project: str, rev: str, label: str, should_abort=None) -> bool:
    """True if this push introduced the build failure, False if it inherited it."""
    return _is_new_label_failure(project, rev, label, "build", should_abort)


def is_new_task_failure(project: str, rev: str, label: str, should_abort=None) -> bool:
    """Whether this push introduced a whole-task failure, compared by task label.

    For a suite that reports no manifests (gtest, jittest, talos, ...) there is no
    group to compare, so the task as a whole is the finest granularity available.
    Only meaningful for an unchunked label: a chunk covers different tests on each
    push, so a chunked task is reported as new rather than compared wrongly.
    """
    if _CHUNKED_LABEL.search(label):
        logger.info(
            "Task %s at %s is chunked, so no ancestor is comparable; running agent",
            label,
            rev,
        )
        return True
    return _is_new_label_failure(
        project, rev, label, "task", should_abort, look_past_pending=True
    )


def new_test_failures(
    project: str,
    rev: str,
    config: tuple[str, str],
    groups: list[str],
    should_abort=None,
) -> set[str]:
    """The failing groups this push introduced, for one configuration.

    `config` is (platform, platform_option). With no configuration to compare
    against, every group is reported as new rather than silently dropped.
    """
    if not all(config):
        logger.info(
            "No configuration for %s; running agent -- %s",
            rev,
            treeherder.push_url(project, rev),
        )
        return set(groups)
    return _await_new_failures(
        project,
        rev,
        lambda ancestor, group: _group_status(project, ancestor, config, group),
        groups,
        "group",
        should_abort,
        look_past_pending=True,
    )
