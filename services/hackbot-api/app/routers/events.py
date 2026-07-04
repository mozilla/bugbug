import base64
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions_applier import apply_pending_actions
from app.auth import require_push_auth
from app.database.connection import get_db
from app.database.models import Run
from app.routers.runs import finalize_run

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/events",
    dependencies=[Depends(require_push_auth)],
)


def _decode_pubsub_push_body(body: dict) -> dict:
    """Decode a standard Pub/Sub push envelope's `message.data` as JSON.

    Both the Eventarc trigger below (agent-run-finished) and the
    `agent-run-events` action-applier subscription deliver via this same
    envelope shape.
    """
    message = body.get("message") or {}
    data = message.get("data")
    if not data:
        return {}
    return json.loads(base64.b64decode(data))


def _cloud_run_execution_name(audit_log_entry: dict) -> str | None:
    """Cloud Run Jobs adapter: pull the Execution resource name from an audit log.

    Eventarc wraps the raw Audit Log entry as the CloudEvent payload; the
    entry's `protoPayload.resourceName` is the full Execution resource name
    (e.g. `projects/P/locations/L/jobs/J/executions/E`), matching what
    `app.jobs.get_execution_status` and `Run.execution_name` already use.

    This is the platform-specific half of `agent_run_finished`. Running agents
    on another platform (e.g. Cloud Batch) later means adding a sibling parser
    that extracts that platform's job identifier from its event, without
    touching the platform-neutral finalize path below.
    """
    proto_payload = audit_log_entry.get("protoPayload") or audit_log_entry
    return proto_payload.get("resourceName")


@router.post("/agent-run-finished", status_code=204)
async def agent_run_finished(
    request: Request, db: AsyncSession = Depends(get_db)
) -> None:
    """Ingress for 'an agent run's underlying execution reached a terminal state'.

    Named by the domain outcome, not the platform mechanism: it's currently
    fed by an Eventarc trigger on Cloud Run Jobs execution state, but the name
    and the finalize path stay valid if agents move to / add another execution
    platform — only the payload parsing (see `_cloud_run_execution_name`) is
    platform-specific.
    """
    body = await request.json()
    event = _decode_pubsub_push_body(body)
    execution_name = _cloud_run_execution_name(event)
    if not execution_name:
        log.warning("agent-run-finished event missing resourceName: %s", event)
        return

    result = await db.execute(select(Run).where(Run.execution_name == execution_name))
    run = result.scalar_one_or_none()
    if run is None:
        log.warning("No run found for execution %s", execution_name)
        return

    await finalize_run(db, run)


@router.post("/apply-run-actions", status_code=204)
async def apply_run_actions(
    request: Request, db: AsyncSession = Depends(get_db)
) -> None:
    """Consumer of the `run.completed` event: apply the run's recorded actions.

    Named for what it does, not the event it consumes, because the same
    `run.completed` event will feed other consumers later (notifications,
    webhooks) — each its own route named after its own job. The subscription
    feeding this one is filtered to succeeded runs (see deploy-events.sh).
    """
    body = await request.json()
    event = _decode_pubsub_push_body(body)
    run_id = event.get("run_id")
    if not run_id:
        log.warning("apply-run-actions event missing run_id: %s", event)
        return

    run = await db.get(Run, uuid.UUID(run_id))
    if run is None:
        log.warning("No run found for run_id %s", run_id)
        return

    await apply_pending_actions(db, run)
