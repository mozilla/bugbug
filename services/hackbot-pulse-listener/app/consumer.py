import logging
from concurrent.futures import Executor

from cachetools import TTLCache
from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

from app import client, taskcluster, worker
from app.config import settings

logger = logging.getLogger(__name__)

CONNECTION_URL = "amqp://{}:{}@pulse.mozilla.org:5671/?ssl=1"

EXCHANGES = ("exchange/taskcluster-queue/v1/task-failed",)

# In-memory dedupe of git revisions already handed to the agent. Only the
# single consumer thread touches it, so no lock is needed.
_seen: TTLCache = TTLCache(
    maxsize=settings.dedupe_max_size, ttl=settings.dedupe_ttl_seconds
)


def process(body: dict, executor: Executor) -> str | None:
    """Handle one Taskcluster failure message. Returns the triggered run id."""
    tags = (body.get("task") or {}).get("tags") or {}

    if tags.get("kind") != "build":
        return None

    project = tags.get("project")
    if project not in settings.watched_repos_set:
        return None

    task_id = body["status"]["taskId"]
    task_name = tags.get("label") or task_id
    developer_email = tags.get("createdForUser")

    revision = taskcluster.get_revision(task_id)
    if not revision:
        logger.warning("No GECKO_HEAD_REV for task %s; skipping", task_id)
        return None

    if revision in _seen:
        logger.info("Revision %s already processed; skipping", revision)
        return None
    _seen[revision] = True

    inputs: dict = {
        "git_commit": revision,
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
        logger.exception("Failed to trigger build-repair run for %s", revision)
        _seen.pop(revision, None)
        return None

    logger.info("Triggered build-repair run %s for %s@%s", run_id, project, revision)
    if run_id is not None:
        executor.submit(
            worker.poll_and_notify, run_id, revision, project, developer_email
        )
    return run_id


def make_handler(executor: Executor):
    def on_message(body, message):
        try:
            process(body, executor)
        except Exception:
            logger.exception("Error handling pulse message")
        finally:
            message.ack()

    return on_message


def _build_queues(user: str) -> list[Queue]:
    queues = []
    for exchange in EXCHANGES:
        suffix = exchange.rsplit("/", 1)[-1]
        queues.append(
            Queue(
                name=f"queue/{user}/build-repair-{suffix}",
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
