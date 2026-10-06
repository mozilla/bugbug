"""Treeherder classification gate for test-repair.

Treeherder ingests the same Taskcluster failures the listener sees, and sheriffs
classify each failing job there, mostly by hand (mozci's autoclassifier covers some
of them). Reading that verdict is cheaper and broader than judging intermittency
ourselves: it is a human triaging every harness, against the push's other results
and the known intermittent bugs.

Two delays are waited out. Ingestion trails the failure message (measured on
autoland: ~50s median, ~3min at p90), so the lookup waits for the job to appear.
Classification then trails the job's end (measured over 40 recent autoland pushes:
~1min median, ~11min at p90, with a long tail past an hour).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15
# Treeherder 502s under load. A failed read makes the caller fail open and spend a
# run, so transient statuses are retried before that happens.
_RETRY_STATUS = {429, 502, 503, 504}
_ATTEMPTS = 3

# Read-through caches, so one ancestor walk fetches each push once instead of once
# per failing group, and the many failing tasks of a push share a fetch. The TTL is
# deliberately shorter than the regression check's poll interval, so a walk waiting
# on an unsettled ancestor still sees fresh results on its next attempt.
_TTL_SECONDS = 60
_group_cache: TTLCache = TTLCache(maxsize=128, ttl=_TTL_SECONDS)
_jobs_cache: TTLCache = TTLCache(maxsize=512, ttl=_TTL_SECONDS)
# A revision never maps to a different push, so this one is held far longer.
_push_id_cache: TTLCache = TTLCache(maxsize=512, ttl=6 * 60 * 60)
# Derived from a parsed log, which no longer changes, so held longer than the rest.
_bug_suggestions_cache: TTLCache = TTLCache(maxsize=512, ttl=30 * 60)
_cache_lock = threading.Lock()

# /api/failureclassification/
_CLASSIFICATIONS = {
    1: "not classified",
    2: "fixed by commit",
    3: "expected fail",
    4: "intermittent",
    5: "infra",
    6: "new failure not classified",
    7: "autoclassified intermittent",
    8: "intermittent needs bugid",
}

# Verdicts that mean this failure is not a new regression worth an agent run.
# The two left out -- "not classified" and "new failure not classified" -- are the
# ones that still may be a real regression.
_NOT_A_REGRESSION = {2, 3, 4, 5, 7, 8}


def push_url(project: str, revision: str | None = None) -> str:
    """Treeherder's job view for a push, or for the project alone without one."""
    url = f"{settings.treeherder_url.rstrip('/')}/#/jobs?repo={project}"
    return f"{url}&revision={revision}" if revision else url


def job_url(project: str, revision: str | None, task_id: str) -> str:
    """Treeherder's job view with one task selected.

    Given no revision Treeherder cannot load the push up front; it still resolves the
    task and offers a link to the push it belongs to.
    """
    return f"{push_url(project, revision)}&selectedTaskRun={task_id}"


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _RETRY_STATUS
    )


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(_ATTEMPTS),
    wait=wait_exponential_jitter(initial=2, max=15, jitter=1),
    before_sleep=before_sleep_log(logger, logging.INFO),
    reraise=True,
)
def _fetch(url: str) -> httpx.Response:
    """GET, retrying the transient failures Treeherder returns under load.

    A caller that cannot read fails open and spends an agent run, so a single 502 --
    routine on this API -- must not be taken as an answer.
    """
    resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp


def _job(project: str, task_id: str) -> dict | None:
    """The Treeherder job for a Taskcluster task, or None if not ingested yet.

    Keyed on the task id, which is the one identifier a failure message carries.
    ``jobs/?revision=`` looks like it would work but silently ignores the filter
    and returns unrelated jobs, so it must not be used here.
    """
    url = (
        f"{settings.treeherder_url.rstrip('/')}/api/project/{project}/jobs/"
        f"?task_id={task_id}"
    )
    results = _fetch(url).json().get("results") or []
    return results[0] if results else None


def _get(path: str) -> dict | list:
    url = f"{settings.treeherder_url.rstrip('/')}/api/project/{path}"
    return _fetch(url).json()


def group_results(
    project: str, revision: str, refresh: bool = False
) -> dict[str, dict[str, bool]]:
    """``{task_id: {group: passed}}`` for a push, as Treeherder recorded it.

    Needs the full 40-character revision; a short one 404s.

    Note a group can be recorded as failing on a task whose job did not fail (wpt
    expected-fail metadata), so this is only meaningful for a task already known to
    have failed.
    """
    key = (project, revision)
    with _cache_lock:
        if not refresh and key in _group_cache:
            return _group_cache[key]

    results = _get(f"{project}/push/group_results/?revision={revision}")
    with _cache_lock:
        _group_cache[key] = results
    return results


def push_id(project: str, revision: str) -> int | None:
    """Treeherder's push id for a revision, or None if it has no push."""
    key = (project, revision)
    with _cache_lock:
        if key in _push_id_cache:
            return _push_id_cache[key]

    results = _get(f"{project}/push/?revision={revision}").get("results") or []
    value = results[0]["id"] if results else None
    with _cache_lock:
        _push_id_cache[key] = value
    return value


def _summarize(job: dict) -> dict:
    return {
        "result": job.get("result"),
        "state": job.get("state"),
        "task_id": job.get("task_id"),
        "platform_option": job.get("platform_option"),
    }


def _push_jobs(project: str, revision: str, query: str, key) -> list[dict]:
    """Cached job query on a push.

    Pending and running jobs are included, which is how a not-yet-finished ancestor
    is told apart from one that never ran the task at all.
    """
    with _cache_lock:
        if key in _jobs_cache:
            return _jobs_cache[key]

    push = push_id(project, revision)
    jobs = []
    if push is not None:
        results = _get(f"{project}/jobs/?push_id={push}&{query}&count=2000")
        jobs = [_summarize(job) for job in results.get("results") or []]
    with _cache_lock:
        _jobs_cache[key] = jobs
    return jobs


def label_jobs(project: str, revision: str, label: str) -> list[dict]:
    """The runs of one exact task label on a push, for build labels.

    Build labels carry no chunk number, so they are stable across pushes.
    """
    return _push_jobs(
        project,
        revision,
        f"job_type_name={quote(label, safe='')}",
        (project, revision, "label", label),
    )


def config_jobs(
    project: str, revision: str, platform: str, platform_option: str
) -> list[dict]:
    """The runs of one configuration on a push -- platform plus build option.

    Not keyed on the task label: a test label carries its chunk number, and chunk
    assignments drift between pushes, so comparing by label makes ancestors look as
    though they never ran the manifest. Platform and option are stable, and a group
    only appears on the tasks that actually ran it, so the group name itself selects
    the right suite.

    ``platform_option`` is filtered here rather than in the query: Treeherder accepts
    the parameter but silently ignores it.
    """
    jobs = _push_jobs(
        project,
        revision,
        f"platform={quote(platform, safe='')}",
        (project, revision, "platform", platform),
    )
    return [job for job in jobs if job["platform_option"] == platform_option]


class GroupResultsUnavailable(Exception):
    """Treeherder records no per-manifest results for a task."""


def failing_groups(project: str, revision: str, task_id: str) -> list[str]:
    """The manifests this task reported as failing.

    An empty list means the task failed nothing at manifest level. Raises when the
    task has no results at all -- which is not the same thing, and must not be
    mistaken for it: a task-level failure (crash, timeout, harness error) or a log
    that is still being parsed would otherwise be silently dropped. Network errors
    propagate for the same reason.
    """
    results = group_results(project, revision)
    if task_id not in results:
        # A cached snapshot can predate this task's ingestion; refetch once before
        # concluding that Treeherder has nothing for it.
        results = group_results(project, revision, refresh=True)
    if task_id not in results:
        raise GroupResultsUnavailable(
            f"Treeherder records no group results for task {task_id}"
        )
    return [
        group
        for group, passed in results[task_id].items()
        if not passed and group.strip() and group != "/"
    ]


def job_for_task(project: str, task_id: str) -> dict | None:
    """The Treeherder job for a failing task, waiting for it to be ingested.

    None when Treeherder never ingests it or cannot be reached, which callers must
    treat as "no verdict" rather than as a reason to drop the failure.
    """
    deadline = time.monotonic() + settings.treeherder_ingest_max_wait_seconds
    while True:
        try:
            job = _job(project, task_id)
        except Exception:
            logger.exception(
                "Treeherder lookup failed for task %s; investigating -- %s",
                task_id,
                job_url(project, None, task_id),
            )
            return None
        if job is not None:
            return job
        if time.monotonic() >= deadline:
            logger.info(
                "Treeherder has not ingested task %s after %ss; investigating -- %s",
                task_id,
                settings.treeherder_ingest_max_wait_seconds,
                job_url(project, None, task_id),
            )
            return None
        time.sleep(settings.treeherder_ingest_poll_seconds)


def await_skip_reason(project: str, task_id: str, job: dict | None) -> str | None:
    """Wait a bounded time for a verdict that this failure is not worth a run.

    ``job`` carries the verdict as of ingestion, which on an intermittent is usually
    still "not classified": a sheriff gets to it a minute or so later. Waiting for it
    here rejects such a failure before the caller's ancestor walk rather than after,
    so the filter no longer depends on how long that walk happens to take.

    Returns None once the wait is spent, so an unclassified failure is investigated
    rather than dropped, and at once for a task Treeherder holds no job for, since
    then there is no verdict to wait for.
    """
    if job is None:
        return None
    reason = skip_reason(job)
    if reason:
        return reason

    deadline = time.monotonic() + settings.treeherder_classification_wait_seconds
    while time.monotonic() < deadline:
        time.sleep(settings.treeherder_ingest_poll_seconds)
        reason = recheck_skip_reason(project, task_id)
        if reason:
            return reason
    return None


def recheck_skip_reason(project: str, task_id: str) -> str | None:
    """Re-read the classification without waiting for ingestion.

    Classification of an intermittent usually lands after we first look, but a
    regression check takes minutes, so it is worth asking again before spending a
    run. Returns None on any error: a failed re-check must not drop the failure.
    """
    try:
        return skip_reason(_job(project, task_id))
    except Exception:
        logger.exception(
            "Treeherder re-check failed for task %s; investigating -- %s",
            task_id,
            job_url(project, None, task_id),
        )
        return None


def skip_reason(job: dict | None) -> str | None:
    """Treeherder's reason not to investigate this failure, or None to proceed.

    Fails open (None) for a missing or unclassified job: an absent verdict must never
    drop a real regression.
    """
    classification = (job or {}).get("failure_classification_id")
    if classification in _NOT_A_REGRESSION:
        return _CLASSIFICATIONS.get(classification, str(classification))
    return None


# Harness noise like "[taskcluster:error] exit status 1" matches unrelated bugs on
# nearly every failing job, so only real failure lines are judged.
_FAILURE_LINE_PREFIX = "TEST-UNEXPECTED-"
_INTERMITTENT_KEYWORD = "intermittent-failure"


def bug_suggestions(project: str, job_id: int) -> list[dict]:
    """Treeherder's bug matches for a job, one entry per parsed failure line."""
    key = (project, job_id)
    with _cache_lock:
        if key in _bug_suggestions_cache:
            return _bug_suggestions_cache[key]

    suggestions = _get(f"{project}/jobs/{job_id}/bug_suggestions/") or []
    # Empty means the log is not parsed yet far more often than it means nothing
    # failed, so only a parsed result is cached; the next reader tries again.
    if suggestions:
        with _cache_lock:
            _bug_suggestions_cache[key] = suggestions
    return suggestions


def await_bug_suggestions(project: str, job: dict | None) -> None:
    """Wait a bounded time for Treeherder to parse the job's log.

    The job is ingested before its log is parsed, and until then ``bug_suggestions``
    is empty: the intermittent gate and the history check both read it, and both
    fall through to the classification wait when it has nothing. Nothing is returned;
    callers read ``bug_suggestions`` themselves, through the cache this fills.
    """
    job_id = (job or {}).get("id")
    if job_id is None:
        return
    deadline = time.monotonic() + settings.treeherder_log_parse_wait_seconds
    while True:
        try:
            if bug_suggestions(project, job_id):
                return
        except Exception:
            logger.exception(
                "Could not read the bug suggestions of job %s -- %s",
                job_id,
                job_url(project, None, (job or {}).get("task_id") or ""),
            )
            return
        if time.monotonic() >= deadline:
            logger.info(
                "Treeherder has not parsed the log of job %s after %ss; judging the "
                "task without its failure lines -- %s",
                job_id,
                settings.treeherder_log_parse_wait_seconds,
                job_url(project, None, (job or {}).get("task_id") or ""),
            )
            return
        time.sleep(settings.treeherder_ingest_poll_seconds)


def _failure_line_test(search: str) -> str | None:
    """The source-relative test path a parsed failure line names, if it names one.

    Lines read ``TEST-UNEXPECTED-FAIL | <test> | <message>``. The middle field is a
    test path for a per-test failure and a label like "ShutdownLeaks" or "shutdown
    hang" for a task-level one; xpcshell prefixes the path with its manifest.
    """
    fields = [field.strip() for field in search.split("|")]
    if len(fields) < 2:
        return None
    candidate = fields[1].rsplit(":", 1)[-1]
    return candidate if "/" in candidate else None


def failing_tests(project: str, job: dict | None) -> list[str] | None:
    """The tests a job reported as failing, or None if they cannot be enumerated.

    None means some unexpected failure was not attributable to a test -- a crash, a
    leak, a shutdown hang, or a job whose log yielded no failure lines at all -- so a
    caller must not read the empty result as "nothing else failed". Fails soft to
    None on any error, for the same reason.
    """
    job_id = (job or {}).get("id")
    if job_id is None:
        return None
    try:
        lines = [
            line.get("search") or ""
            for line in bug_suggestions(project, job_id)
            if (line.get("search") or "").startswith(_FAILURE_LINE_PREFIX)
        ]
    except Exception:
        logger.exception(
            "Could not read the bug suggestions of job %s -- %s",
            job_id,
            job_url(project, None, (job or {}).get("task_id") or ""),
        )
        return None

    if not lines:
        logger.info(
            "Job %s has no parsed failure lines; its tests cannot be judged -- %s",
            job_id,
            job_url(project, None, (job or {}).get("task_id") or ""),
        )
        return None
    tests = [_failure_line_test(line) for line in lines]
    if None in tests:
        logger.info(
            "Job %s failed outside any test (%s); its tests cannot be judged -- %s",
            job_id,
            lines[tests.index(None)][:120],
            job_url(project, None, (job or {}).get("task_id") or ""),
        )
        return None
    return list(dict.fromkeys(tests))


@dataclass(frozen=True)
class IntermittentMatch:
    """What Treeherder's bug suggestions say about a failing job.

    ``known`` means every unexpected-failure line is both already seen in this
    revision and matched to one of ``bug_ids``.
    """

    bug_ids: list[int] = field(default_factory=list)
    known: bool = False


def intermittent_match(project: str, job: dict | None) -> IntermittentMatch:
    """Read the bug suggestions of a failing job and judge it a known intermittent.

    Both signals are required: genuine regressions also match open intermittent bugs,
    so the bug alone would drop them. Fails open on any error or missing data.
    """
    job_id = (job or {}).get("id")
    if job_id is None:
        return IntermittentMatch()

    try:
        lines = [
            line
            for line in bug_suggestions(project, job_id)
            if (line.get("search") or "").startswith(_FAILURE_LINE_PREFIX)
        ]
        if not lines:
            return IntermittentMatch()

        bug_ids: list[int] = []
        known = True
        for line in lines:
            matched = _open_intermittent_bugs(line)
            bug_ids += [bug for bug in matched if bug not in bug_ids]
            # A missing flag counts as new, never as known.
            if not matched or line.get("failure_new_in_rev", True):
                known = False
        return IntermittentMatch(bug_ids, known)
    except Exception:
        logger.exception(
            "Could not read the bug suggestions of job %s; investigating -- %s",
            job_id,
            job_url(project, None, (job or {}).get("task_id") or ""),
        )
        return IntermittentMatch()


def _open_intermittent_bugs(suggestion: dict) -> list[int]:
    """Ids of the unresolved intermittent-failure bugs a failure line matches."""
    bugs = suggestion.get("bugs") or {}
    matched = []
    for bug in (bugs.get("open_recent") or []) + (bugs.get("all_others") or []):
        keywords = (bug.get("keywords") or "").split(",")
        if bug.get("id") and not bug.get("resolution"):
            if _INTERMITTENT_KEYWORD in [k.strip() for k in keywords]:
                matched.append(bug["id"])
    return matched
