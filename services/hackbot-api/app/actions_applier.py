"""Record and (optionally) apply a run's actions once it has finished.

On run completion the recorded actions from `summary["actions"]` are always
upserted as `run_actions` rows (one per entry) so they're visible and
manageable in the UI. Whether they're then applied *automatically* depends on
the agent's `auto_apply_actions` opt-in (see `app/agents.py`); either way they
can be applied on demand (manual apply-all from the UI). Application runs each
pending row through the handler registry in `hackbot_runtime.actions.handlers`
and is idempotent per action — an already-`applied` row is never re-applied, so
Pub/Sub retries and repeated manual applies are safe.
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
from app.agents import AGENT_REGISTRY
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


async def ensure_action_rows(
    db: AsyncSession, run: Run
) -> list[tuple[RunAction, list[dict]]]:
    """Upsert one `RunAction` per recorded action (does not apply them).

    Returns each row paired with its (not persisted) attachments list from
    summary.json. Idempotent: existing rows are reused, so this can run on
    every completion and again on each manual apply.
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


async def _apply_pending_rows(
    db: AsyncSession, run: Run, rows: list[tuple[RunAction, list[dict]]]
) -> None:
    """Apply every not-yet-`applied` row in `rows`, committing per action.

    Cross-action `{{actions.<ref>.<field>}}` placeholders resolve against rows
    that are already `applied` (seeded from prior applies) plus ones applied
    earlier in this pass, so a later (even manual) apply can still reference an
    earlier action's result.
    """
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


async def on_run_completed(db: AsyncSession, run: Run) -> None:
    """Record a completed run's actions, and auto-apply them if the agent opts in.

    Called from the `apply-run-actions` push route. Actions are always recorded
    (so the UI can show/manually apply them); they're applied automatically only
    when the run's agent has `auto_apply_actions=True`.
    """
    # Defense-in-depth: only a succeeded run's actions are recorded/applied. A
    # failed/timed-out run may have recorded actions before erroring, but acting
    # on a run that never reached a verified-good state isn't wanted. The
    # Pub/Sub subscription already filters to status="succeeded"; this keeps the
    # function correct if invoked directly.
    if run.status != RunStatus.succeeded.value:
        log.info("Skipping actions for run %s (status=%s)", run.run_id, run.status)
        return

    rows = await ensure_action_rows(db, run)
    await db.commit()

    spec = AGENT_REGISTRY.get(run.agent)
    if spec and spec.auto_apply_actions:
        await _apply_pending_rows(db, run, rows)
    else:
        log.info(
            "Recorded %d action(s) for run %s; auto-apply off for agent %s",
            len(rows),
            run.run_id,
            run.agent,
        )


async def apply_all_pending(db: AsyncSession, run: Run) -> None:
    """Apply all of a run's not-yet-`applied` actions on demand (manual).

    Ensures the rows exist first, so this works whether or not they were
    recorded automatically on completion.
    """
    rows = await ensure_action_rows(db, run)
    await db.commit()
    await _apply_pending_rows(db, run, rows)
