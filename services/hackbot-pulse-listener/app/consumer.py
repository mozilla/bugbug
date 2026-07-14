import logging
import threading
from concurrent.futures import Executor

from cachetools import TTLCache
from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

from app import client, lando, regression, taskcluster, worker
from app.config import settings
from app.models import RunContext

logger = logging.getLogger(__name__)

CONNECTION_URL = "amqp://{}:{}@pulse.mozilla.org:5671/?ssl=1"

EXCHANGES = ("exchange/taskcluster-queue/v1/task-failed",)

# In-memory dedupe of hg revisions already handed to the agent. A revision is
# recorded only once we actually trigger a run, so an inherited failure on one
# build label never suppresses a genuine regression on another label of the
# same push, while a revision that breaks many builds still triggers only once.
# Messages are handled on worker threads, so the check-and-record is done under
# a lock.
_seen: TTLCache = TTLCache(
    maxsize=settings.dedupe_max_size, ttl=settings.dedupe_ttl_seconds
)
_seen_lock = threading.Lock()


def process(body: dict, executor: Executor) -> str | None:
    """Handle one Taskcluster failure message. Returns the triggered run id."""
    tags = (body.get("task") or {}).get("tags") or {}

    task_label = tags.get("label") or ""
    if "build" not in task_label or "test" in task_label:
        return None

    project = tags.get("project")
    if project not in settings.watched_repos_set:
        return None

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
        with _seen_lock:
            _seen.pop(hg_revision, None)
        return None

    inputs: dict = {
        "git_commit": git_commit,
        "failure_tasks": {task_name: task_id},
        "run_try_push": settings.run_try_push,
    }
    if settings.model:
        inputs["model"] = settings.model
    if settings.max_turns is not None:
        inputs["max_turns"] = settings.max_turns

    try:
        run_id = client.trigger_run(inputs)
    except Exception:
        logger.exception("Failed to trigger build-repair run for %s", hg_revision)
        with _seen_lock:
            _seen.pop(hg_revision, None)
        return None

    logger.info(
        "Triggered build-repair run %s for %s@%s (git %s)",
        run_id,
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
    # durable queue and steal each other's messages. Production keeps the plain
    # name for continuity.
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
