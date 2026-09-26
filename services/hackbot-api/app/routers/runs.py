import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BeforeValidator, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import gcs, jobs, pubsub
from app.action_handlers.registry import PATCH_ACTION_TYPES
from app.actions_applier import apply_all_pending
from app.agents import AGENT_REGISTRY, AgentSpec, model_to_env
from app.auth import require_api_key
from app.config import settings
from app.database.connection import get_db
from app.database.models import Run, RunAction
from app.jobs import ExecutionStatus
from app.schemas import (
    AgentDescriptor,
    ArtifactRef,
    RunActionDoc,
    RunDoc,
    RunRef,
    RunStatus,
    RunSummary,
)

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)])

_PATCH_ARTIFACT = "changes/changes.patch"


def _normalize_identity(email: str | None) -> str | None:
    if not email:
        return None

    return email.strip().lower() or None


UserEmail = Annotated[str | None, BeforeValidator(_normalize_identity)]

DedupeKey = (
    Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=200,
            to_lower=True,
            ascii_only=True,
        ),
    ]
    | None
)


def _lookup_agent(name: str) -> AgentSpec:
    agent = AGENT_REGISTRY.get(name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown agent '{name}'",
        )
    return agent


@router.get("/agents")
async def list_agents() -> list[AgentDescriptor]:
    return [
        AgentDescriptor(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema.model_json_schema(),
        )
        for spec in AGENT_REGISTRY.values()
    ]


@router.post(
    "/agents/{agent_name}/runs",
    response_model=RunRef,
    status_code=201,
    response_description="A new run, started by this request.",
    responses={
        status.HTTP_200_OK: {
            "model": RunRef,
            "description": (
                "A run already exists for this agent and dedupe key, so it is "
                "returned instead of creating a new one."
            ),
        },
    },
)
async def create_run(
    agent_name: str,
    payload: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    on_behalf_of: Annotated[
        UserEmail,
        Header(
            alias="X-On-Behalf-Of",
            description="Email of the user this run is requested for.",
        ),
    ] = None,
    dedupe_key: Annotated[
        DedupeKey,
        Query(
            description=(
                "A key to deduplicate runs for the same work. If a run already "
                "exists for this key, it will be returned instead of creating "
                "a new one."
            ),
        ),
    ] = None,
) -> RunRef | Response:
    agent = _lookup_agent(agent_name)
    try:
        inputs = agent.input_schema.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = uuid.uuid4()
    results_prefix = gcs.run_prefix(str(run_id))

    claim = (
        pg_insert(Run)
        .values(
            run_id=run_id,
            agent=agent.name,
            status=RunStatus.pending.value,
            inputs=inputs.model_dump(mode="json"),
            requested_by=on_behalf_of,
            dedupe_key=dedupe_key,
            results_prefix=results_prefix,
            artifacts=[],
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key", "agent"])
        .returning(Run)
    )
    run = await db.scalar(claim)

    if run is None:
        # RETURNING only yields rows it inserted, so nothing came back: the
        # index turned this insert away, which only a supplied key can cause.
        result = await db.execute(
            select(Run).where(Run.agent == agent.name, Run.dedupe_key == dedupe_key)
        )
        run = result.scalar_one()
        log.info(
            "Deduplicated %s request for key %r onto run %s (%s)",
            run.agent,
            run.dedupe_key,
            run.run_id,
            run.status,
        )

        return Response(
            content=RunRef.model_validate(run).model_dump_json(),
            media_type="application/json",
            status_code=status.HTTP_200_OK,
        )

    # Publishes the claim to the other API instances before this request does
    # anything slow.
    await db.commit()

    try:
        policy = await gcs.generate_results_policy(str(run_id))
        env_overrides: dict[str, str] = {
            "RUN_ID": str(run_id),
            "RESULTS_BUCKET": settings.results_bucket,
            "RESULTS_PREFIX": results_prefix,
            "RESULTS_POLICY_URL": policy["url"],
            "RESULTS_POLICY_FIELDS": json.dumps(policy["fields"]),
            **(agent.build_env or model_to_env)(inputs),
        }
        execution_name = await jobs.trigger_execution(agent.job_name, env_overrides)
    except Exception as exc:
        log.exception("Failed to start execution for run %s", run_id)
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


@router.get("/runs")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    agent: Annotated[str | None, Query()] = None,
    # Aliased so the query param is `status` without shadowing fastapi.status.
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    requested_by: Annotated[
        UserEmail,
        Query(description="Only return runs requested by this user."),
    ] = None,
    dedupe_key: Annotated[
        DedupeKey,
        Query(description="Only return runs carrying this dedupe key."),
    ] = None,
) -> list[RunDoc]:
    stmt = select(Run)
    if agent is not None:
        stmt = stmt.where(Run.agent == agent)
    if status_filter is not None:
        stmt = stmt.where(Run.status == status_filter.value)
    if requested_by is not None:
        stmt = stmt.where(Run.requested_by == requested_by)
    if dedupe_key is not None:
        # The trigger's own handle on its run: a caller that keyed the work can
        # find it again without having stored the run id.
        stmt = stmt.where(Run.dedupe_key == dedupe_key)
    # created_at is the sort key; run_id is a deterministic tiebreaker so offset
    # paging is stable when timestamps collide. (agent/status/requested_by and
    # created_at are all indexed, so filtering + ordering stay index-backed.)
    stmt = (
        stmt.order_by(Run.created_at.desc(), Run.run_id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [RunDoc.model_validate(r) for r in result.scalars()]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> RunDoc:
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
    db: Annotated[AsyncSession, Depends(get_db)],
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


@router.get("/runs/{run_id}/actions")
async def list_run_actions(
    run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[RunActionDoc]:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return await _list_actions(db, run_id)


@router.post("/runs/{run_id}/actions/apply")
async def apply_run_actions(
    run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
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

    try:
        exec_status = await jobs.get_execution_status(run.execution_name)
    except Exception:
        log.warning("Failed to fetch execution status for run %s", run.run_id)
        raise

    if exec_status == ExecutionStatus.pending:
        return
    if exec_status == ExecutionStatus.running:
        if run.status == RunStatus.pending.value:
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

    agent_spec = AGENT_REGISTRY.get(run.agent)
    if (
        new_status == RunStatus.succeeded
        and agent_spec is not None
        and agent_spec.warn_on_unsubmitted_patch
        and _has_unsubmitted_patch(summary, artifacts)
    ):
        log.error(
            "Agent run produced code changes without submitting a patch "
            "(run_id=%s, agent=%s)",
            run.run_id,
            run.agent,
        )
    await pubsub.publish_run_completed(str(run.run_id), run.agent, run.status)


def _has_unsubmitted_patch(
    summary: RunSummary | None,
    artifacts: list[ArtifactRef],
) -> bool:
    """Whether a run produced source changes without a patch action."""
    has_patch_artifact = any(artifact.name == _PATCH_ARTIFACT for artifact in artifacts)
    has_patch_action = summary is not None and any(
        action["type"] in PATCH_ACTION_TYPES for action in summary.actions
    )
    return has_patch_artifact and not has_patch_action


def _terminal_status(
    exec_status: ExecutionStatus, summary: RunSummary | None
) -> tuple[RunStatus, str | None]:
    if exec_status == ExecutionStatus.unknown:
        return RunStatus.failed, "Run was never associated with an execution"

    if exec_status == ExecutionStatus.cancelled:
        return RunStatus.timed_out, "Execution was cancelled or timed out"
    if summary is None:
        if exec_status == ExecutionStatus.gone:
            return RunStatus.failed, (
                "Execution record was deleted and no summary.json was written, "
                "so the run's outcome cannot be recovered"
            )
        return RunStatus.failed, "Execution finished without writing summary.json"
    if summary.status != "ok":
        return RunStatus.failed, summary.error
    if exec_status not in (ExecutionStatus.succeeded, ExecutionStatus.gone):
        return RunStatus.failed, "Execution exited non-zero despite summary status=ok"
    return RunStatus.succeeded, None
