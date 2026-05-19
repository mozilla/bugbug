import asyncio
import logging
from enum import Enum
from functools import lru_cache

from google.cloud import run_v2

from app.config import settings

log = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


@lru_cache(maxsize=1)
def _jobs_client() -> run_v2.JobsClient:
    return run_v2.JobsClient()


@lru_cache(maxsize=1)
def _executions_client() -> run_v2.ExecutionsClient:
    return run_v2.ExecutionsClient()


def _job_resource_name(job_name: str) -> str:
    if not settings.gcp_project or not settings.gcp_region:
        raise RuntimeError("gcp_project and gcp_region must be configured")
    return f"projects/{settings.gcp_project}/locations/{settings.gcp_region}/jobs/{job_name}"


_AGENT_CONTAINER_NAME = "agent"


def _trigger_sync(job_name: str, env_overrides: dict[str, str]) -> str:
    # Each agent's Job manifest declares two containers: `agent` (no
    # tokens) and `broker` (holds tokens, fully configured at deploy
    # time). Per-execution env overrides target only the agent
    # container by name so the broker's env (Secret Manager-backed) is
    # untouched.
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                name=_AGENT_CONTAINER_NAME,
                env=[run_v2.EnvVar(name=k, value=v) for k, v in env_overrides.items()],
            )
        ],
        timeout={"seconds": settings.job_execution_timeout_seconds},
        task_count=1,
    )
    request = run_v2.RunJobRequest(
        name=_job_resource_name(job_name),
        overrides=overrides,
    )
    operation = _jobs_client().run_job(request=request)
    return operation.metadata.name


async def trigger_execution(job_name: str, env_overrides: dict[str, str]) -> str:
    return await asyncio.to_thread(_trigger_sync, job_name, env_overrides)


def _execution_status_sync(execution_name: str) -> ExecutionStatus:
    execution = _executions_client().get_execution(name=execution_name)
    if execution.completion_time:
        if execution.succeeded_count and not execution.failed_count:
            return ExecutionStatus.succeeded
        if execution.cancelled_count:
            return ExecutionStatus.cancelled
        return ExecutionStatus.failed
    if execution.running_count or execution.start_time:
        return ExecutionStatus.running
    return ExecutionStatus.pending


async def get_execution_status(execution_name: str) -> ExecutionStatus:
    return await asyncio.to_thread(_execution_status_sync, execution_name)
