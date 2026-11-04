import asyncio
import logging
from enum import Enum
from functools import lru_cache

from google.api_core.exceptions import NotFound
from google.cloud import run_v2

from app.config import settings

log = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    gone = "gone"
    unknown = "unknown"


@lru_cache(maxsize=1)
def _jobs_client() -> run_v2.JobsClient:
    return run_v2.JobsClient()


@lru_cache(maxsize=1)
def _executions_client() -> run_v2.ExecutionsClient:
    return run_v2.ExecutionsClient()


def _job_resource_name(job_name: str) -> str:
    if not settings.gcp_project or not settings.gcp_region:
        raise RuntimeError("gcp_project and gcp_region must be configured")
    return (
        f"projects/{settings.gcp_project}/locations/{settings.gcp_region}"
        f"/jobs/{job_name}{settings.agent_job_suffix}"
    )


_AGENT_CONTAINER_NAME = "agent"
_BROKER_CONTAINER_NAME = "broker"


def _broker_env() -> dict[str, str]:
    """Env the API hands every broker on top of its deploy-time config."""
    env: dict[str, str] = {}
    if settings.phabricator.edge_key:
        env["PHABRICATOR_EDGE_KEY"] = settings.phabricator.edge_key
    return env


def _has_broker(job_resource_name: str) -> bool:
    job = _jobs_client().get_job(name=job_resource_name)
    return any(
        container.name == _BROKER_CONTAINER_NAME
        for container in job.template.template.containers
    )


def _env_vars(env: dict[str, str]) -> list[run_v2.EnvVar]:
    return [run_v2.EnvVar(name=k, value=v) for k, v in env.items()]


def _trigger_sync(job_name: str, env_overrides: dict[str, str]) -> str:
    job_resource_name = _job_resource_name(job_name)
    container_overrides = [
        run_v2.RunJobRequest.Overrides.ContainerOverride(
            name=_AGENT_CONTAINER_NAME, env=_env_vars(env_overrides)
        )
    ]
    broker_env = _broker_env()
    if broker_env and _has_broker(job_resource_name):
        container_overrides.append(
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                name=_BROKER_CONTAINER_NAME, env=_env_vars(broker_env)
            )
        )
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=container_overrides,
        timeout={"seconds": settings.job_execution_timeout_seconds},
        task_count=1,
    )
    request = run_v2.RunJobRequest(
        name=job_resource_name,
        overrides=overrides,
    )
    operation = _jobs_client().run_job(request=request)
    return operation.metadata.name


async def trigger_execution(job_name: str, env_overrides: dict[str, str]) -> str:
    return await asyncio.to_thread(_trigger_sync, job_name, env_overrides)


def _execution_status_sync(execution_name: str) -> ExecutionStatus:
    try:
        execution = _executions_client().get_execution(name=execution_name)
    except NotFound:
        log.warning("Execution %s no longer exists", execution_name)
        return ExecutionStatus.gone

    if execution.completion_time:
        if execution.succeeded_count and not execution.failed_count:
            return ExecutionStatus.succeeded
        if execution.cancelled_count:
            return ExecutionStatus.cancelled
        return ExecutionStatus.failed
    if execution.running_count or execution.start_time:
        return ExecutionStatus.running
    return ExecutionStatus.pending


async def get_execution_status(execution_name: str | None) -> ExecutionStatus:
    if execution_name is None:
        return ExecutionStatus.unknown

    return await asyncio.to_thread(_execution_status_sync, execution_name)
