"""Apply a run's recorded actions once it has finished.

Triggered by the `agent-run-events` push subscription (see
`app/routers/events.py`), after `finalize_run` has already persisted the
run's terminal status and `summary.json`. Reads `summary["actions"]`, upserts
one `RunAction` row per entry, and runs each pending one through the handler
registry in `hackbot_runtime.actions.handlers` — idempotent per action, so a
Pub/Sub retry of the same message only re-attempts actions that didn't reach
`applied` last time.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from hackbot_runtime.actions.handlers import ApplyContext, get_handler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import gcs
from app.database.models import Run, RunAction
from app.schemas import RunStatus

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{actions\.([^.}]+)\.([^}]+)\}\}")


def resolve_placeholders(value: Any, results_by_ref: dict[str, dict]) -> Any:
    """Substitute `{{actions.<ref>.<field>}}` in `value` using prior results.

    Recurses through dicts/lists so a placeholder can appear anywhere in an
    action's params, not just at the top level. A placeholder referencing a
    ref that hasn't been applied yet (or lacks that field) is left as-is
    rather than raising — the action then fails downstream with an error a
    human can actually read, instead of a silent substitution glitch.
    """
    if isinstance(value, str):

        def _sub(match: re.Match) -> str:
            result = results_by_ref.get(match.group(1))
            if result is None or match.group(2) not in result:
                return match.group(0)
            return str(result[match.group(2)])

        return _PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, results_by_ref) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, results_by_ref) for v in value]
    return value


async def _ensure_rows(
    db: AsyncSession, run: Run
) -> list[tuple[RunAction, list[dict]]]:
    """Upsert one `RunAction` per recorded action.

    Returns each row paired with its (not persisted) attachments list from
    summary.json.
    """
    actions: list[dict] = (run.summary or {}).get("actions", [])

    result = await db.execute(select(RunAction).where(RunAction.run_id == run.run_id))
    existing = {row.idx: row for row in result.scalars()}

    rows: list[tuple[RunAction, list[dict]]] = []
    for idx, action in enumerate(actions):
        row = existing.get(idx)
        if row is None:
            row = RunAction(
                run_id=run.run_id,
                idx=idx,
                type=action["type"],
                params=action.get("params", {}),
                ref=action.get("ref"),
                status="pending",
            )
            db.add(row)
        rows.append((row, action.get("attachments", [])))
    await db.flush()
    return rows


async def apply_pending_actions(db: AsyncSession, run: Run) -> None:
    # Only a successful run's actions are applied. A failed/timed-out run may
    # have recorded actions before it errored, but submitting a patch or
    # posting a comment from a run that never reached a verified-good state
    # isn't wanted. The applier's Pub/Sub subscription filters to
    # status="succeeded" as the primary routing (failed runs never reach here),
    # so this check is defense-in-depth: it keeps the applier correct when
    # invoked directly (tests, future callers) and documents the policy in
    # code. `RunCompleted` is still published for every terminal status (see
    # finalize_run), so a future failure-notifier consumer can filter for the
    # ones this applier ignores.
    if run.status != RunStatus.succeeded.value:
        log.info(
            "Skipping action application for run %s (status=%s)",
            run.run_id,
            run.status,
        )
        return

    rows = await _ensure_rows(db, run)

    results_by_ref: dict[str, dict] = {
        row.ref: row.result
        for row, _ in rows
        if row.ref and row.status == "applied" and row.result is not None
    }

    for row, attachments in rows:
        if row.status == "applied":
            continue

        handler = get_handler(row.type)
        if handler is None:
            row.status = "failed"
            row.error = f"No handler registered for action type '{row.type}'"
            await db.commit()
            continue

        params = resolve_placeholders(row.params, results_by_ref)
        ctx = ApplyContext(
            run_id=str(run.run_id),
            download_artifact=lambda key, run_id=str(run.run_id): (
                gcs.download_artifact_bytes(run_id, key)
            ),
            attachments=attachments,
        )

        try:
            outcome = await handler.apply(params, ctx)
        except Exception as exc:
            log.exception(
                "Handler for %s raised while applying run %s action #%d",
                row.type,
                run.run_id,
                row.idx,
            )
            row.status = "failed"
            row.error = str(exc)
            await db.commit()
            continue

        row.status = outcome.status
        row.result = outcome.result
        row.error = outcome.error
        row.applied_at = datetime.now(timezone.utc)
        await db.commit()

        if row.status == "applied" and row.ref and row.result is not None:
            results_by_ref[row.ref] = row.result
