"""Record and (optionally) apply a run's actions once it has finished.

On run completion the recorded actions from `summary["actions"]` are always
upserted as `run_actions` rows (one per entry) so they're visible and
manageable in the UI. Whether they're then applied *automatically* is decided by
`_auto_apply_blocker` (see `app/agents.py`); either way they
can be applied on demand (manual apply-all from the UI). Application runs each pending
row through the handler registry in `hackbot_runtime.actions.handlers` and is
idempotent per action — an already-`applied` row is never re-applied, so Pub/Sub
retries and repeated manual applies are safe.
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
from app.agents import AGENT_REGISTRY, AgentSpec
from app.database.models import Run, RunAction
from app.schemas import RunStatus

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{actions\.([^.}]+)\.([^}]+)\}\}")


def _collect_refs(value: Any) -> set[str]:
    """Every ref named by `{{actions.<ref>.<field>}}` placeholders in `value`.

    Recurses through dicts/lists the same way :func:`resolve_placeholders`
    does, so one action can depend on multiple referenced actions.
    """
    if isinstance(value, str):
        return {match.group(1) for match in _PLACEHOLDER_RE.finditer(value)}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        refs: set[str] = set()
        for item in value:
            refs |= _collect_refs(item)
        return refs
    return set()


def _order_units_by_dependencies(dependencies: list[set[int]]) -> list[int]:
    """Order units after their dependencies, preserving order among ready units.

    If no unit can progress, append the stuck remainder in its original order
    because no dependency-respecting order exists for it.
    """
    ordered: list[int] = []
    remaining = set(range(len(dependencies)))

    while remaining:
        progressed = False
        for unit_id in range(len(dependencies)):
            if unit_id in remaining and dependencies[unit_id].isdisjoint(remaining):
                ordered.append(unit_id)
                remaining.remove(unit_id)
                progressed = True

        if not progressed:
            ordered.extend(sorted(remaining))
            break

    return ordered


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


def _auto_apply_blocker(spec: AgentSpec | None, run: Run) -> str | None:
    """Why `run`'s recorded actions need a human, or None if they may be applied.

    This holds no policy about what an agent may record. That is bounded on the agent
    side, as it records, where a refusal reaches the agent as a tool error it can
    correct in the same run — frontend-triage does it with action hooks. Whether a
    given result is safe to apply is also the agent's call, since only it knows how
    sure it was; it reports that as `findings.auto_apply`. This function honors both
    and fails closed.
    """
    if spec is None or not spec.auto_apply_actions:
        return "auto-apply is off for this agent"

    # `is not True`, so a run that reports no verdict — or something that isn't a
    # boolean — is held rather than read as consent.
    findings = (run.summary or {}).get("findings") or {}
    if spec.auto_apply_requires_consent and findings.get("auto_apply") is not True:
        return "the agent did not mark this result safe to apply unattended"

    return None


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
        agent=run.agent,
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
    earlier action's result. When the reference graph is acyclic, actions
    defining refs apply before actions that name them, and recorded (idx) order
    is kept among actions whose references are already satisfied.
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
    # Each coalesced group applies as one unit, sitting where its last member
    # is (rows are in idx order, so that's its max idx); every other row is a
    # unit of its own. Units hold indices into `pending`.
    anchor_of = {i: max(group) for group in groups for i in group}
    group_at = {max(group): group for group in groups}
    units: list[list[int]] = []
    for pos in range(len(pending)):
        anchor = anchor_of.get(pos)
        if anchor is None:
            units.append([pos])
        elif pos == anchor:
            units.append(group_at[anchor])

    async def _apply_unit(unit: list[int]) -> None:
        if len(unit) > 1:
            member_rows = [pending[i][0] for i in unit]
            entries = [
                (member.type, resolve_placeholders(member.params, results_by_ref))
                for member in member_rows
            ]
            outcome = await _dispatch(
                run, "bugzilla.update_bug", merge_resolved(entries), []
            )
        else:
            row, attachments = pending[unit[0]]
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

    # Map each ref to its pending producer. Unknown refs retain the existing
    # resolver behavior: they are logged and left in the payload.
    producer_by_ref: dict[str, int] = {}
    for unit_id, unit in enumerate(units):
        for i in unit:
            ref = pending[i][0].ref
            if ref:
                producer_by_ref[ref] = unit_id

    # One unit may reference several actions, and several units may consume the
    # same ref. Each ref is expected to identify one producer.
    dependencies: list[set[int]] = []
    for unit in units:
        refs: set[str] = set()
        for i in unit:
            refs |= _collect_refs(pending[i][0].params)
        dependencies.append(
            {producer_by_ref[ref] for ref in refs if ref in producer_by_ref}
        )

    for unit_id in _order_units_by_dependencies(dependencies):
        await _apply_unit(units[unit_id])


async def on_run_completed(db: AsyncSession, run: Run) -> None:
    """Record a completed run's actions, and auto-apply them if the agent qualifies.

    Called from the `apply-run-actions` push route. Actions are always recorded (so the
    UI can show/manually apply them); they're applied automatically only when
    `_auto_apply_blocker` finds nothing in the way.
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
    blocker = _auto_apply_blocker(spec, run)
    if blocker is None:
        await _apply_pending_rows(db, run, rows)
        return

    log.info(
        "Recorded %d action(s) for run %s; holding for review: %s (agent %s)",
        len(rows),
        run.run_id,
        blocker,
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
