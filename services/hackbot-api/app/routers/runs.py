import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import gcs, jobs
from app.agents import AGENT_REGISTRY, AgentSpec
from app.auth import require_api_key
from app.config import settings
from app.database.connection import get_db
from app.database.models import Run
from app.jobs import ExecutionStatus
from app.schemas import (
    TERMINAL_STATUSES,
    AgentDescriptor,
    RunDoc,
    RunRef,
    RunStatus,
    RunSummary,
)

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)])


def _lookup_agent(name: str) -> AgentSpec:
    agent = AGENT_REGISTRY.get(name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown agent '{name}'",
        )
    return agent


@router.get("/agents", response_model=list[AgentDescriptor])
async def list_agents() -> list[AgentDescriptor]:
    return [
        AgentDescriptor(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema.model_json_schema(),
        )
        for spec in AGENT_REGISTRY.values()
    ]


@router.post("/agents/{agent_name}/runs", response_model=RunRef, status_code=201)
async def create_run(
    agent_name: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> RunRef:
    agent = _lookup_agent(agent_name)
    try:
        inputs = agent.input_schema.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = uuid.uuid4()
    results_prefix = gcs.run_prefix(str(run_id))

    policy = await gcs.generate_results_policy(str(run_id))

    run = Run(
        run_id=run_id,
        agent=agent.name,
        status=RunStatus.pending.value,
        inputs=inputs.model_dump(mode="json"),
        results_prefix=results_prefix,
        artifacts=[],
    )
    db.add(run)
    await db.flush()

    env_overrides: dict[str, str] = {
        "RUN_ID": str(run_id),
        "RESULTS_BUCKET": settings.results_bucket,
        "RESULTS_PREFIX": results_prefix,
        "RESULTS_POLICY_URL": policy["url"],
        "RESULTS_POLICY_FIELDS": json.dumps(policy["fields"]),
        **agent.build_env(inputs),
    }

    try:
        execution_name = await jobs.trigger_execution(agent.job_name, env_overrides)
    except Exception as exc:
        log.exception("Failed to trigger Cloud Run Job for run %s", run_id)
        run.status = RunStatus.failed.value
        run.error = f"Failed to start execution: {exc}"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to start agent execution",
        ) from exc

    run.execution_name = execution_name
    await db.commit()

    return RunRef.model_validate(run)


@router.get("/runs/{run_id}", response_model=RunDoc)
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> RunDoc:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status in {s.value for s in TERMINAL_STATUSES} or run.execution_name is None:
        return RunDoc.model_validate(run)

    await _reconcile(db, run)
    return RunDoc.model_validate(run)


@router.get("/runs/{run_id}/artifacts/{artifact_path:path}")
async def get_artifact_download_url(
    run_id: uuid.UUID,
    artifact_path: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Return a short-lived signed URL to download one artifact.

    The artifact must be one already listed on the run, which both scopes the
    download to this run's results prefix and prevents path traversal / probing
    of unrelated objects.
    """
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Refresh artifacts for runs that may have completed since the last poll,
    # so a freshly finished run's artifacts are visible here without first
    # requiring a GET /runs/{run_id} call.
    if (
        run.status not in {s.value for s in TERMINAL_STATUSES}
        and run.execution_name is not None
    ):
        await _reconcile(db, run)

    known = {a.get("name") for a in (run.artifacts or [])}
    if artifact_path not in known:
        raise HTTPException(status_code=404, detail="Artifact not found")

    url = await gcs.generate_artifact_download_url(str(run_id), artifact_path)
    if url is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return {"url": url}


async def _reconcile(db: AsyncSession, run: Run) -> None:
    assert run.execution_name is not None
    try:
        exec_status = await jobs.get_execution_status(run.execution_name)
    except Exception:
        log.exception("Failed to fetch execution status for run %s", run.run_id)
        return

    if exec_status in (ExecutionStatus.pending, ExecutionStatus.running):
        if (
            run.status == RunStatus.pending.value
            and exec_status == ExecutionStatus.running
        ):
            run.status = RunStatus.running.value
            await db.commit()
        return

    summary = await gcs.read_summary(str(run.run_id))
    artifacts = await gcs.list_artifacts(str(run.run_id))

    new_status, error = _terminal_status(exec_status, summary)

    run.status = new_status.value
    run.artifacts = [a.model_dump(mode="json") for a in artifacts]
    if summary is not None:
        run.summary = summary.model_dump(mode="json")
    if error is not None:
        run.error = error

    await db.commit()


def _terminal_status(
    exec_status: ExecutionStatus, summary: RunSummary | None
) -> tuple[RunStatus, str | None]:
    if exec_status == ExecutionStatus.cancelled:
        return RunStatus.timed_out, "Execution was cancelled or timed out"
    if summary is None:
        return RunStatus.failed, "Execution finished without writing summary.json"
    if summary.status != "ok":
        return RunStatus.failed, summary.error
    if exec_status != ExecutionStatus.succeeded:
        return RunStatus.failed, "Execution exited non-zero despite summary status=ok"
    return RunStatus.succeeded, None
