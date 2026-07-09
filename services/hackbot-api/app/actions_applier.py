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

from hackbot_runtime.actions.handlers import (
    ActionResult,
    ApplyContext,
    get_handler,
    merge_resolved,
    plan_coalesced_groups,
)
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


async def _dispatch(
    run: Run, action_type: str, params: dict, attachments: list[dict]
) -> ActionResult:
    """Run one handler call, converting failures into a failed `ActionResult`.

    A missing handler or a raised exception becomes a failed result so callers
    can stamp the affected row(s) uniformly.
    """
    handler = get_handler(action_type)
    if handler is None:
        return ActionResult.failed(
            f"No handler registered for action type '{action_type}'"
        )

    ctx = ApplyContext(
        run_id=str(run.run_id),
        download_artifact=lambda key, run_id=str(run.run_id): (
            gcs.download_artifact_bytes(run_id, key)
        ),
        attachments=attachments,
    )
    try:
        return await handler.apply(params, ctx)
    except Exception as exc:
        log.exception(
            "Handler for %s raised while applying run %s", action_type, run.run_id
        )
        return ActionResult.failed(str(exc))


async def _apply_pending_rows(
    db: AsyncSession, run: Run, rows: list[tuple[RunAction, list[dict]]]
) -> None:
    """Apply every not-yet-`applied` row in `rows`, committing per action.

    Same-bug Bugzilla field changes are coalesced with the closest comment into
    a single `PUT /bug/{id}` so Bugzilla applies them as one transaction (one
    bugmail, one history entry); any other comments on that bug still apply
    separately. See `plan_coalesced_groups`/`merge_resolved` in the runtime lib.

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

    pending = [(row, att) for row, att in rows if row.status != "applied"]

    # Plan which pending rows coalesce into one bug PUT (indices into `pending`).
    # Drop any group whose rows carry a `ref`: nothing should reference a
    # coalesced member's result, and this keeps that invariant if a ref is ever
    # added to a bug action. Everything else applies one row at a time as before.
    groups = [
        group
        for group in plan_coalesced_groups(
            [(row.type, row.params) for row, _ in pending]
        )
        if all(pending[i][0].ref is None for i in group)
    ]
    # Rows sit in idx order, so a group's last member is its max idx: apply the
    # whole group there, once every earlier (backward) dependency is resolved.
    anchor_of = {i: max(group) for group in groups for i in group}
    group_at = {max(group): group for group in groups}

    for pos, (row, attachments) in enumerate(pending):
        anchor = anchor_of.get(pos)
        if anchor is not None and pos != anchor:
            continue  # non-anchor member: applied together with its anchor

        if anchor is not None:
            member_rows = [pending[i][0] for i in group_at[anchor]]
            entries = [
                (member.type, resolve_placeholders(member.params, results_by_ref))
                for member in member_rows
            ]
            outcome = await _dispatch(
                run, "bugzilla.update_bug", merge_resolved(entries), []
            )
        else:
            member_rows = [row]
            params = resolve_placeholders(row.params, results_by_ref)
            outcome = await _dispatch(run, row.type, params, attachments)

        # Only stamp applied_at on a real success, so a failed row isn't
        # mistaken for one that was applied.
        applied_at = datetime.now(timezone.utc) if outcome.status == "applied" else None
        for member in member_rows:
            member.status = outcome.status
            member.result = outcome.result
            member.error = outcome.error
            if applied_at is not None:
                member.applied_at = applied_at
        await db.commit()

        if outcome.status == "applied" and outcome.result is not None:
            for member in member_rows:
                if member.ref:
                    results_by_ref[member.ref] = outcome.result


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
