import logging
import threading
from concurrent.futures import Executor

from cachetools import TTLCache
from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

from app import client, lando, regression, taskcluster, treeherder, worker
from app.config import settings
from app.models import RunContext

logger = logging.getLogger(__name__)

CONNECTION_URL = "amqp://{}:{}@pulse.mozilla.org:5671/?ssl=1"

EXCHANGES = ("exchange/taskcluster-queue/v1/task-failed",)

# Taskcluster ``kind`` tags that denote test tasks (vs build tasks).
TEST_KINDS = {"test", "mochitest", "web-platform-tests", "source-test"}

# In-memory dedupe of hg revisions already handed to the build-repair agent. A
# revision is recorded only once we actually trigger a run, so an inherited
# failure on one build label never suppresses a genuine regression on another
# label of the same push, while a revision that breaks many builds still triggers
# only once. Messages are handled on worker threads, so the check-and-record is
# done under a lock.
_seen: TTLCache = TTLCache(
    maxsize=settings.dedupe_max_size, ttl=settings.dedupe_ttl_seconds
)
_seen_lock = threading.Lock()

# Independent dedupe for test-repair runs, keyed by (hg revision, test group): one
# push emits many failing test tasks, and we want one run per failing group. Keyed on
# the revision rather than the Taskcluster task group because backfills and retriggers
# are dispatched by action tasks, which start task groups of their own -- the same
# push would otherwise be investigated once per task group.
_seen_tests: TTLCache = TTLCache(
    maxsize=settings.dedupe_max_size, ttl=settings.dedupe_ttl_seconds
)
_seen_tests_lock = threading.Lock()


def _is_test_task(tags: dict) -> bool:
    return tags.get("kind") in TEST_KINDS or bool(tags.get("test-suite"))


def process(body: dict, executor: Executor) -> str | None:
    """Handle one Taskcluster failure message. Returns the triggered run id."""
    tags = (body.get("task") or {}).get("tags") or {}

    project = tags.get("project")
    if project not in settings.watched_repos_set:
        logger.debug("Ignoring failure on unwatched project %s", project)
        return None

    task_label = tags.get("label") or ""
    if "build" in task_label and "test" not in task_label:
        return _process_build(body, tags, executor)
    if _is_test_task(tags):
        return _process_test(body, tags, executor)
    logger.debug("Ignoring non-build, non-test task %s", task_label)
    return None


def _release(cache: TTLCache, lock: threading.Lock, keys) -> None:
    """Give up claimed dedupe keys so a later message can retry them."""
    with lock:
        for key in keys:
            cache.pop(key, None)


def _process_build(body: dict, tags: dict, executor: Executor) -> str | None:
    """Build-failure path: trigger the build-repair agent."""
    project = tags.get("project")
    task_label = tags.get("label") or ""
    task_id = body["status"]["taskId"]
    task_name = tags.get("label") or task_id
    developer_email = tags.get("createdForUser")

    hg_revision = taskcluster.get_hg_revision(task_id)
    if not hg_revision:
        logger.warning("No GECKO_HEAD_REV for task %s; skipping", task_id)
        return None

    with _seen_lock:
        already_seen = hg_revision in _seen
    if already_seen:
        logger.info("Revision %s already triggered a run; skipping", hg_revision)
        return None

    if not regression.is_new_build_failure(project, hg_revision, task_label):
        logger.info(
            "Build %s at %s inherited from an ancestor push; skipping",
            task_label,
            hg_revision,
        )
        return None

    with _seen_lock:
        if hg_revision in _seen:
            logger.info("Revision %s already triggered a run; skipping", hg_revision)
            return None
        _seen[hg_revision] = True

    git_commit = lando.hg_to_git(hg_revision)
    if not git_commit:
        logger.warning(
            "Could not map hg revision %s to git for task %s (%s); skipping",
            hg_revision,
            task_id,
            project,
        )
        _release(_seen, _seen_lock, [hg_revision])
        return None

    try:
        run_id = client.trigger_run(
            {
                "failure_tasks": {task_name: task_id},
                "run_try_push": settings.run_try_push,
            }
        )
    except Exception:
        logger.exception("Failed to trigger build-repair run for %s", hg_revision)
        _release(_seen, _seen_lock, [hg_revision])
        return None

    logger.info(
        "%s build-repair for %s@%s (git %s)",
        f"Triggered run {run_id}" if run_id else "Would trigger",
        project,
        hg_revision,
        git_commit,
    )
    if run_id is not None:
        ctx = RunContext(
            run_id=run_id,
            repo=project,
            git_commit=git_commit,
            hg_revision=hg_revision,
            task_id=task_id,
            developer_email=developer_email,
        )
        executor.submit(worker.poll_and_notify, ctx)
    return run_id


def _process_test(body: dict, tags: dict, executor: Executor) -> str | None:
    """Test-failure path: filter, then trigger the test-repair agent for the task.

    One push emits many failing test tasks; each task may fail several groups. We
    resolve the failing groups and keep the ones that are genuine, non-intermittent
    regressions, then trigger a single run for the task (the agent resolves the
    commit range itself from the task id). Dedupe is per (push, group) so a manifest
    failing across chunks is investigated once.
    """
    status = body.get("status") or {}
    task_id = status.get("taskId")
    project = tags.get("project")
    label = tags.get("label") or task_id
    developer_email = tags.get("createdForUser")

    hg_revision = taskcluster.get_hg_revision(task_id)
    if not hg_revision:
        logger.warning("No GECKO_HEAD_REV for test task %s; skipping", task_id)
        return None

    # Cheapest gate first: one small request, and it rules out intermittents and
    # infra failures for every harness before any ancestor walking. The same record
    # carries the configuration the regression check compares against.
    job = treeherder.job_for_task(project, task_id)
    reason = treeherder.skip_reason(job)
    if reason:
        logger.info("Treeherder classified task %s as %s; skipping", task_id, reason)
        return None
    config = ((job or {}).get("platform"), (job or {}).get("platform_option"))

    whole_task = (project, hg_revision, task_id, label, developer_email)
    try:
        groups = treeherder.failing_groups(project, hg_revision, task_id)
    except treeherder.GroupResultsUnavailable as exc:
        # Routine: a task-level failure (crash, timeout, harness error) produces no
        # per-manifest results, and a log may still be being parsed. Either way we
        # cannot say what failed, so fail open rather than drop it.
        logger.info("%s; running the agent on the whole task", exc)
        return _trigger_whole_task(*whole_task, executor)
    except Exception:
        logger.exception(
            "Could not read the failing groups of task %s; "
            "running the agent on the whole task",
            task_id,
        )
        return _trigger_whole_task(*whole_task, executor)

    if not groups:
        logger.info("Task %s reported no failing test groups; skipping", task_id)
        return None

    claimed = _claim_groups([(hg_revision, group) for group in groups])
    if not claimed:
        logger.info(
            "Every failing group of task %s is already handled; skipping", task_id
        )
        return None

    fresh = _fresh_groups(
        [group for group in groups if (hg_revision, group) in claimed],
        project,
        hg_revision,
        config,
    )
    if not fresh:
        logger.info("No new, non-intermittent groups for task %s; skipping", task_id)
        return None

    # Ask again before spending a run: the check above takes minutes, which is about
    # how long Treeherder takes to classify an intermittent, so a verdict that was
    # not available at the start of it often is by now.
    reason = treeherder.recheck_skip_reason(project, task_id)
    if reason:
        logger.info(
            "Treeherder classified task %s as %s while it was being checked; skipping",
            task_id,
            reason,
        )
        return None

    return _trigger_test_repair(
        fresh, claimed, project, hg_revision, task_id, label, developer_email, executor
    )


def _trigger_whole_task(
    project: str,
    hg_revision: str,
    task_id: str,
    label: str,
    developer_email: str | None,
    executor: Executor,
) -> str | None:
    """Investigate a task whose failing manifests could not be determined.

    There is no manifest to compare against an ancestor here, so nothing else delays
    the decision. In practice these are mostly intermittents and expected failures
    that Treeherder classifies a few minutes later, so wait for a verdict before
    spending a run.
    """
    claimed = _claim_groups([(hg_revision, label)])
    if not claimed:
        logger.info("Task %s is already handled; skipping", task_id)
        return None

    reason = treeherder.await_skip_reason(project, task_id)
    if reason:
        logger.info("Treeherder classified task %s as %s; skipping", task_id, reason)
        return None

    return _trigger_test_repair(
        [], claimed, project, hg_revision, task_id, label, developer_email, executor
    )


def _claim_groups(keys: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Claim the keys not yet handed off, returning the ones this call claimed.

    Claimed *before* the regression check, not after: every failing chunk
    of a push resolves the same groups, so without an up-front claim they all run
    the expensive walk concurrently and then throw the answer away. A key stays
    claimed even when the filter then rejects the group, so the sibling tasks do
    not recompute a verdict that would come out the same.
    """
    with _seen_tests_lock:
        claimed = {k for k in keys if k not in _seen_tests}
        for k in claimed:
            _seen_tests[k] = True
    return claimed


def _fresh_groups(
    candidates: list[str], project: str, hg_revision: str, config: tuple[str, str]
) -> list[str]:
    """The groups whose failure this push introduced."""
    new = regression.new_test_failures(project, hg_revision, config, candidates)
    for group in candidates:
        if group not in new:
            logger.info(
                "Group %s at %s inherited from an ancestor; skipping",
                group,
                hg_revision,
            )
    return [group for group in candidates if group in new]


def _trigger_test_repair(
    test_groups: list[str],
    claimed,
    project: str,
    hg_revision: str,
    task_id: str,
    label: str,
    developer_email: str | None,
    executor: Executor,
) -> str | None:
    try:
        run_id = client.trigger_run(
            {"failure_tasks": {label: task_id}},
            agent_name=settings.test_repair_agent_name,
        )
    except Exception:
        logger.exception("Failed to trigger test-repair run for task %s", task_id)
        _release(_seen_tests, _seen_tests_lock, claimed)
        return None

    logger.info(
        "%s test-repair for %s task %s (%s) at %s",
        f"Triggered run {run_id}" if run_id else "Would trigger",
        project,
        task_id,
        f"{len(test_groups)} group(s)" if test_groups else "groups unresolved",
        hg_revision,
    )
    if run_id is not None:
        git_commit = lando.hg_to_git(hg_revision)
        if not git_commit:
            # Not fatal, unlike on the build path: the agent works from the task id
            # alone, and a revision Lando has not mirrored yet is routine for a
            # just-landed push. The notification omits the git revision instead of
            # linking to an empty commit.
            logger.warning(
                "Could not map hg revision %s to git for task %s; "
                "the notification will omit the git revision",
                hg_revision,
                task_id,
            )
        ctx = RunContext(
            run_id=run_id,
            repo=project,
            git_commit=git_commit or "",
            hg_revision=hg_revision,
            task_id=task_id,
            developer_email=developer_email,
            agent=settings.test_repair_agent_name,
            test_groups=list(test_groups),
        )
        executor.submit(worker.poll_and_notify, ctx)
    return run_id


def make_handler(executor: Executor):
    def run(body: dict) -> None:
        try:
            process(body, executor)
        except Exception:
            logger.exception("Error handling pulse message")

    def on_message(body, message):
        # Process on a worker thread so a regression check that blocks waiting
        # for a parent build to settle never stalls the consumer thread.
        try:
            executor.submit(run, body)
        except Exception:
            logger.exception("Failed to dispatch pulse message")
        finally:
            message.ack()

    return on_message


def _build_queues(user: str) -> list[Queue]:
    # Both local and prod authenticate as the same pulse user, so the queue name
    # must also vary by environment; otherwise both consumers bind to the same
    # durable queue and steal each other's messages.
    env = settings.environment
    env_segment = "" if env == "production" else f"{env}-"
    queues = []
    for exchange in EXCHANGES:
        suffix = exchange.rsplit("/", 1)[-1]
        queues.append(
            Queue(
                name=f"queue/{user}/build-repair-{env_segment}{suffix}",
                exchange=Exchange(exchange, type="topic", no_declare=True),
                routing_key="#",
                durable=True,
                auto_delete=True,
            )
        )
    return queues


class BuildFailureConsumer(ConsumerMixin):
    def __init__(self, connection, queues, on_message):
        self.connection = connection
        self.queues = queues
        self.on_message = on_message

    def get_consumers(self, Consumer, channel):
        return [Consumer(queues=self.queues, callbacks=[self.on_message])]


def build_consumer(executor: Executor) -> BuildFailureConsumer:
    connection = Connection(
        CONNECTION_URL.format(settings.pulse_user, settings.pulse_password)
    )
    return BuildFailureConsumer(
        connection, _build_queues(settings.pulse_user), make_handler(executor)
    )
