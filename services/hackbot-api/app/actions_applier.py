"""Record and (optionally) apply a run's actions once it has finished.

On run completion the recorded actions from `summary["actions"]` are always
upserted as `run_actions` rows (one per entry) so they're visible and
manageable in the UI. Whether they're then applied *automatically* depends on the
agent's `auto_apply_actions` opt-in (see `app/agents.py`); either way they can be
applied on demand (manual apply-all from the UI). Application runs each pending row
through the handler registry in `hackbot_runtime.actions.handlers`.

Applying is safe to repeat: an already-`applied` row is never re-applied, and a row is
locked before its handler is called and stays locked until the result is committed. A
crash between Bugzilla accepting a write and that commit still rolls back to a retryable
row — Bugzilla offers no idempotency key, so the choice is between a possible duplicate
and a possible silent loss.
"""

from __future__ import annotations

import json
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
from sqlalchemy.dialects.postgresql import insert
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
    human can actually read, instead of a silent substitution glitch. Any such
    unresolved placeholder is logged as an error so the literal text landing in
    a posted comment doesn't go unnoticed.
    """
    if isinstance(value, str):

        def _sub(match: re.Match) -> str:
            placeholder = match.group(0)
            ref = match.group(1)
            field = match.group(2)

            if ref not in results_by_ref:
                log.warning(
                    "Unresolved action reference %s: no applied action with "
                    "ref '%s' (left as-is)",
                    placeholder,
                    ref,
                )
                return placeholder

            ref_result = results_by_ref[ref]
            if field not in ref_result:
                log.warning(
                    "Unresolved action reference %s: action '%s' has no field "
                    "'%s' (available: %s) (left as-is)",
                    placeholder,
                    ref,
                    field,
                    ", ".join(sorted(ref_result)) or "none",
                )
                return placeholder

            return str(ref_result[field])

        return _PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, results_by_ref) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, results_by_ref) for v in value]
    return value


class UnresolvedReference(Exception):
    """A placeholder survived substitution, so the action must not be sent."""


def _resolved_params(row: RunAction, results_by_ref: dict[str, dict]) -> Any:
    """`row`'s params with placeholders substituted, or raise if any survived.

    `resolve_placeholders` leaves an unresolvable `{{actions.<ref>.<field>}}` in place
    so a human can see what went wrong, which is only safe if it never reaches
    Bugzilla — otherwise the literal text lands in a real bug comment with a log line
    as the only signal. Raising makes it a failed row a human can retry instead.
    """
    resolved = resolve_placeholders(row.params or {}, results_by_ref)
    leftover = _PLACEHOLDER_RE.findall(json.dumps(resolved, default=str))
    if leftover:
        raise UnresolvedReference(
            "refers to "
            + ", ".join(
                sorted(f"{{{{actions.{ref}.{field}}}}}" for ref, field in leftover)
            )
            + ", which has not been applied"
        )
    return resolved


async def ensure_action_rows(
    db: AsyncSession, run: Run
) -> list[tuple[RunAction, list[dict]]]:
    """Upsert one `RunAction` per recorded action (does not apply them).

    Returns each row paired with its (not persisted) attachments list from
    summary.json. Idempotent: existing rows are reused, so this can run on
    every completion and again on each manual apply.
    """
    actions = (run.summary or {}).get("actions", [])
    if not isinstance(actions, list) or not actions:
        return []

    # An unusable action is skipped rather than raised on: with no dead-letter topic, a
    # raise here would 5xx the push route and the same message would return for the whole
    # retention window. Indices are preserved so `ref` placeholders and the coalescing
    # order stay meaningful.
    usable = [
        (idx, action)
        for idx, action in enumerate(actions)
        if isinstance(action, dict)
        and isinstance(action.get("type"), str)
        # An explicit `null` is fine — normalised to `{}` below — but `params` is NOT
        # NULL, so any other non-dict would raise on insert.
        and isinstance(action.get("params") or {}, dict)
    ]
    if len(usable) != len(actions):
        log.error(
            "Run %s recorded %d unusable action(s) (no type, or params that aren't "
            "a mapping); skipping them",
            run.run_id,
            len(actions) - len(usable),
        )
    if not usable:
        return []

    # `ON CONFLICT DO NOTHING` rather than select-then-insert: two concurrent first
    # deliveries can both find no rows and both insert the same `(run_id, idx)`, and the
    # loser would 500 the route — turning the concurrency this path exists to tolerate
    # into an error.
    await db.execute(
        insert(RunAction)
        .values(
            [
                {
                    "run_id": run.run_id,
                    "idx": idx,
                    "type": action["type"],
                    "params": action.get("params") or {},
                    "ref": action.get("ref"),
                    "status": "pending",
                }
                for idx, action in usable
            ]
        )
        .on_conflict_do_nothing(constraint="uq_run_actions_run_idx")
    )
    await db.flush()

    result = await db.execute(select(RunAction).where(RunAction.run_id == run.run_id))
    by_idx = {row.idx: row for row in result.scalars()}
    return [
        (by_idx[idx], action.get("attachments", []))
        for idx, action in usable
        if idx in by_idx
    ]


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


async def _lock_unapplied(db: AsyncSession, member_rows: list[RunAction]) -> bool:
    """Lock `member_rows` for applying. True if this caller should go on to dispatch.

    The lock is held until the caller commits the result, so a second delivery blocks
    here and then reads `applied` instead of posting the same comment again. Holding it
    rather than marking the rows and letting go is what makes a crash self-healing: a
    process that dies mid-dispatch rolls back, leaving the rows exactly as retryable as
    they were, with no in-progress state for anything to reclaim.

    Locking rather than a conditional UPDATE, because a `WHERE ... AND (SELECT count(*)
    ...) = n` reads the statement's snapshot: two claimants of a two-row group can each
    lock a different member and then each skip the one the other took, leaving the group
    unapplied by either. Ordered by id so two overlapping groups can't deadlock.

    `populate_existing` because the session runs with `expire_on_commit=False` and these
    rows are already in its identity map, so the SELECT would otherwise hand back the
    pre-lock cached copies — judging the predicate against exactly the stale state the
    lock exists to rule out.
    """
    ids = sorted(member.id for member in member_rows)
    result = await db.execute(
        select(RunAction)
        .where(RunAction.id.in_(ids))
        .order_by(RunAction.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked = list(result.scalars())

    if len(locked) == len(ids) and all(row.status != "applied" for row in locked):
        return True

    # Nothing to do, so let the lock go rather than hold it for the rest of the pass.
    await db.commit()
    return False


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
    # Guarded because it indexes into params the agent wrote, so a surprising shape
    # raises here. Coalescing is only an optimisation (one bugmail instead of two), so
    # failing to plan it degrades to applying singly — where each bad row fails alone.
    try:
        groups = [
            group
            for group in plan_coalesced_groups(
                # `or {}`: a pre-existing row could have null params.
                [(row.type, row.params or {}) for row, _ in pending]
            )
            if all(pending[i][0].ref is None for i in group)
        ]
    except Exception:
        log.exception(
            "Could not plan coalescing for run %s; applying its actions singly",
            run.run_id,
        )
        groups = []
    # Rows sit in idx order, so a group's last member is its max idx: apply the
    # whole group there, once every earlier (backward) dependency is resolved.
    anchor_of = {i: max(group) for group in groups for i in group}
    group_at = {max(group): group for group in groups}

    for pos, (row, attachments) in enumerate(pending):
        anchor = anchor_of.get(pos)
        if anchor is not None and pos != anchor:
            continue  # non-anchor member: applied together with its anchor

        member_rows = (
            [pending[i][0] for i in group_at[anchor]] if anchor is not None else [row]
        )

        # Lock before dispatching, or two concurrent deliveries both see `pending` and
        # both post the comment to the bug. Also covers a manual apply-all racing the
        # automatic one.
        if not await _lock_unapplied(db, member_rows):
            log.info(
                "Rows %s of run %s were already applied; skipping",
                [member.idx for member in member_rows],
                run.run_id,
            )
            continue

        # `merge_resolved` also indexes into agent-written params, so it can raise
        # outside `_dispatch`'s guard. A failed row a human can read beats 5xxing the
        # push route, which with no dead-letter topic replays for the retention window.
        try:
            if anchor is not None:
                entries = [
                    (member.type, _resolved_params(member, results_by_ref))
                    for member in member_rows
                ]
                outcome = await _dispatch(
                    run, "bugzilla.update_bug", merge_resolved(entries), []
                )
            else:
                outcome = await _dispatch(
                    run,
                    row.type,
                    _resolved_params(row, results_by_ref),
                    attachments,
                )
        except Exception as exc:
            log.exception(
                "Could not build the request for rows %s of run %s",
                [member.idx for member in member_rows],
                run.run_id,
            )
            outcome = ActionResult.failed(str(exc))

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

    Called from the `apply-run-actions` push route. Actions are always recorded (so the
    UI can show/manually apply them); they're applied automatically only when the run's
    agent has `auto_apply_actions=True`.
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
