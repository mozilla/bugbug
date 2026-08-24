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
_BROKER_CONTAINER_NAME = "broker"

# The only variables settable on the credentialed `broker` container. What
# keeps a run's inputs out of it is that everything else targets `agent`, and
# this allowlist is what keeps that true as new overrides get added.
_BROKER_ENV_ALLOWLIST = frozenset({"BUGZILLA_SCOPE_TOKEN"})


def _container_override(
    name: str, env: dict[str, str]
) -> run_v2.RunJobRequest.Overrides.ContainerOverride:
    return run_v2.RunJobRequest.Overrides.ContainerOverride(
        name=name,
        env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
    )


def _trigger_sync(
    job_name: str,
    env_overrides: dict[str, str],
    broker_env: dict[str, str] | None = None,
) -> str:
    # Each agent's Job declares two containers: `agent` (no tokens) and
    # `broker` (holds tokens, configured at deploy time). Per-execution
    # overrides target `agent` so the broker's Secret Manager-backed env is
    # untouched. `broker_env` is the exception, carrying the run's capability
    # token, and is allowlisted by name above.
    broker_env = broker_env or {}
    disallowed = set(broker_env) - _BROKER_ENV_ALLOWLIST
    if disallowed:
        raise ValueError(
            f"refusing to set {sorted(disallowed)} on the broker container; "
            f"only {sorted(_BROKER_ENV_ALLOWLIST)} may be overridden there"
        )

    containers = [_container_override(_AGENT_CONTAINER_NAME, env_overrides)]
    if broker_env:
        containers.append(_container_override(_BROKER_CONTAINER_NAME, broker_env))

    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=containers,
        timeout={"seconds": settings.job_execution_timeout_seconds},
        task_count=1,
    )
    request = run_v2.RunJobRequest(
        name=_job_resource_name(job_name),
        overrides=overrides,
    )
    operation = _jobs_client().run_job(request=request)
    return operation.metadata.name


async def trigger_execution(
    job_name: str,
    env_overrides: dict[str, str],
    broker_env: dict[str, str] | None = None,
) -> str:
    return await asyncio.to_thread(_trigger_sync, job_name, env_overrides, broker_env)


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
