import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import gcs, jobs, pubsub
from app.actions_applier import apply_all_pending
from app.agents import AGENT_REGISTRY, AgentSpec, model_to_env
from app.auth import require_api_key
from app.config import settings
from app.database.connection import get_db
from app.database.models import Run, RunAction
from app.jobs import ExecutionStatus
from app.schemas import (
    AgentDescriptor,
    RunActionDoc,
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
        **(agent.build_env or model_to_env)(inputs),
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


@router.get("/runs", response_model=list[RunDoc])
async def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    agent: str | None = Query(default=None),
    # Aliased so the query param is `status` without shadowing fastapi.status.
    status_filter: RunStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> list[RunDoc]:
    stmt = select(Run)
    if agent is not None:
        stmt = stmt.where(Run.agent == agent)
    if status_filter is not None:
        stmt = stmt.where(Run.status == status_filter.value)
    # created_at is the sort key; run_id is a deterministic tiebreaker so offset
    # paging is stable when timestamps collide. (agent/status/created_at are all
    # indexed, so filtering + ordering stay index-backed.)
    stmt = (
        stmt.order_by(Run.created_at.desc(), Run.run_id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [RunDoc.model_validate(r) for r in result.scalars()]


@router.get("/runs/{run_id}", response_model=RunDoc)
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> RunDoc:
    # A plain DB read: completion is detected out-of-band by finalize_run,
    # invoked from the Eventarc-triggered /internal/events/agent-run-finished
    # route (see app/routers/events.py), not from this request.
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
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

    known = {a.get("name") for a in (run.artifacts or [])}
    if artifact_path not in known:
        raise HTTPException(status_code=404, detail="Artifact not found")

    url = await gcs.generate_artifact_download_url(str(run_id), artifact_path)
    if url is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return {"url": url}


async def _list_actions(db: AsyncSession, run_id: uuid.UUID) -> list[RunActionDoc]:
    result = await db.execute(
        select(RunAction).where(RunAction.run_id == run_id).order_by(RunAction.idx)
    )
    return [RunActionDoc.model_validate(r) for r in result.scalars()]


@router.get("/runs/{run_id}/actions", response_model=list[RunActionDoc])
async def list_run_actions(
    run_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[RunActionDoc]:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return await _list_actions(db, run_id)


@router.post("/runs/{run_id}/actions/apply", response_model=list[RunActionDoc])
async def apply_run_actions(
    run_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[RunActionDoc]:
    """Manually apply all of a run's pending actions (apply-all).

    Idempotent — already-applied actions are skipped — so this is safe to
    click again after a partial failure. Returns the actions' updated state.
    """
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    await apply_all_pending(db, run)
    return await _list_actions(db, run_id)


async def finalize_run(db: AsyncSession, run: Run) -> None:
    """Bring `run` to its terminal state and publish RunCompleted, once.

    Invoked from the Eventarc-triggered agent-run-finished route instead
    of from a client request. Idempotent via `finalized_at`, since Eventarc's
    at-least-once delivery can call this more than once for the same run.
    """
    if run.finalized_at is not None:
        return

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
    run.finalized_at = datetime.now(timezone.utc)

    await db.commit()
    await pubsub.publish_run_completed(str(run.run_id), run.agent, run.status)


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
