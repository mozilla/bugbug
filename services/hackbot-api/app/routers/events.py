import base64
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from hackbot_client import HackbotClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import notifications
from app.actions_applier import on_run_completed
from app.auth import require_push_auth
from app.database.connection import get_db
from app.database.models import Run
from app.hackbot_client import get_hackbot_client
from app.routers.runs import finalize_run

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/events",
    dependencies=[Depends(require_push_auth)],
)


def _decode_pubsub_push_body(body: dict) -> dict:
    """Decode a Pub/Sub push envelope's `message.data` as JSON."""
    message = body.get("message") or {}
    data = message.get("data")
    if not data:
        return {}
    return json.loads(base64.b64decode(data))


def _execution_name_from_completion_log(entry: dict) -> str | None:
    """Cloud Run Jobs adapter: pull the Execution name from a completion LogEntry.

    A logging sink routes the control-plane `system_event` audit log that fires
    when an execution's `Completed` condition changes (success or failure);
    Pub/Sub delivers that LogEntry as the message body. The execution resource
    name can appear in a few places depending on the log format, so check the
    likely ones. Correlation to `Run.execution_name` (set from the run_v2
    execution name) tolerates prefix differences via a suffix match in the
    route, so returning any of these forms is fine.

    Only this half is Cloud-Run-specific: a future platform adds a sibling
    parser feeding the same platform-neutral finalize path below.
    """
    proto_payload = entry.get("protoPayload") or {}
    response = proto_payload.get("response") or {}
    metadata = response.get("metadata") or {}
    labels = entry.get("labels") or {}
    return (
        proto_payload.get("resourceName")
        or metadata.get("name")
        or labels.get("run.googleapis.com/execution_name")
    )


async def _find_run_for_execution(db: AsyncSession, execution_name: str) -> Run | None:
    """Find the Run for an execution event's execution name.

    Prefers an exact match on the stored execution_name; falls back to matching
    the execution short-name (last path segment) as a suffix, so a v1
    (`namespaces/...`) vs v2 (`projects/.../executions/E`) prefix mismatch
    between the log entry and what run_v2 stored doesn't break correlation. It
    also handles container logs, which only carry the short-name.
    """
    result = await db.execute(select(Run).where(Run.execution_name == execution_name))
    run = result.scalar_one_or_none()
    if run is not None:
        return run

    short = execution_name.rsplit("/", 1)[-1]
    if short:
        result = await db.execute(
            select(Run).where(Run.execution_name.like(f"%/{short}"))
        )
        run = result.scalar_one_or_none()
    return run


async def _handle_execution_event(
    request: Request, db: AsyncSession, event_name: str
) -> None:
    """Resolve an execution lifecycle event to its Run and sync the Run's status.

    Shared by the started and finished routes. `finalize_run` checks the real
    execution status, so either event can finalize the run, and the requester is
    notified by whichever one does.
    """
    body = await request.json()
    event = _decode_pubsub_push_body(body)
    execution_name = _execution_name_from_completion_log(event)
    if not execution_name:
        log.warning("%s event missing execution name: %s", event_name, event)
        return

    run = await _find_run_for_execution(db, execution_name)
    if run is None:
        log.warning("No run found for execution %s", execution_name)
        return

    notification_candidate = not run.finalized_at and run.requested_by
    await finalize_run(db, run)
    if notification_candidate and run.finalized_at is not None:
        await notifications.notify_requester(run)


@router.post("/agent-run-started", status_code=204)
async def agent_run_started(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    """Ingress for 'an agent run's underlying execution started running'.

    Without it a run stays `pending` for its whole execution, because the API
    otherwise only hears about the execution when it ends. Cloud Run's only
    `system_event` audit log for an execution is written when it completes, so
    this is fed by a Cloud Logging sink on the container log line
    hackbot-runtime writes at the start of every run (`RUN_STARTED_MARKER`).
    """
    await _handle_execution_event(request, db, "agent-run-started")


@router.post("/agent-run-finished", status_code=204)
async def agent_run_finished(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    """Ingress for 'an agent run's underlying execution reached a terminal state'.

    Named by the domain outcome, not the platform mechanism: it's fed by a
    Cloud Logging sink on Cloud Run Jobs `system_event` completion logs (which
    fire for success and failure alike, incl. OOM/crash). The name and the
    finalize path stay valid if agents move to / add another execution platform
    — only the payload parsing (see `_execution_name_from_completion_log`) is
    platform-specific. `finalize_run` re-queries the authoritative status, so
    this route just needs to identify which run finished.
    """
    await _handle_execution_event(request, db, "agent-run-finished")


@router.post("/apply-run-actions", status_code=204)
async def apply_run_actions(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    hackbot_client: Annotated[HackbotClient, Depends(get_hackbot_client)],
) -> None:
    """Consumer of `run.completed`: record the run's actions, auto-apply if opted in.

    Named for what it does, not the event it consumes. The subscription feeding
    this route is filtered to succeeded runs (see deploy-events.sh).
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

    await on_run_completed(db, run, hackbot_client)
