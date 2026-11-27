"""Durable CI handoff for perf investigations, using the existing run lifecycle.

Pulse is a wake-up hint. Taskcluster gates the short validation execution;
Treeherder and Perfherder in the agent still decide the performance verdict.
The maintenance sweep reconciles lost/early events and delayed indexing.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import actions_applier, gcs, jobs, notifications, pubsub
from app.action_handlers import try_server_handler
from app.agents import AGENT_REGISTRY, model_to_env
from app.config import settings
from app.database.models import Run
from app.schemas import RunStatus, RunSummary

log = logging.getLogger(__name__)
# The investigation records its candidate push as this action.
TRY_PUSH_ACTION_TYPE = "try_server.push"
TREEHERDER = "https://treeherder.mozilla.org"
QUEUE = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1"
# A validation execution may wait ~15 minutes for Perfherder ingestion and
# then clone Firefox to apply a fixed candidate.
VALIDATION_SECONDS = 3600
EVENT_MIN_INTERVAL = 15
MAX_LAUNCH_FAILURES = 5
# Validation executions that ran but crashed or timed out.
MAX_VALIDATION_FAILURES = 3
MAX_BACKOFF_SECONDS = 3600
# How long past its CI deadline a run may keep failing to validate.
DEADLINE_GRACE_SECONDS = 6 * 60 * 60


def pending_watch(summary: RunSummary) -> dict | None:
    """The CI deadline of an investigation that handed its try push to the API."""
    if summary.findings.get("status") != "awaiting_ci":
        return None
    deadline = (summary.findings.get("ci_pending") or {}).get("deadline")
    if (
        not isinstance(deadline, (int, float))
        or not 0 < deadline <= time.time() + 86400
    ):
        raise ValueError("Invalid performance CI continuation")
    return {"deadline": deadline}


class TryPushFailed(Exception):
    """The investigation's recorded try push was refused or failed to apply."""


async def _try_push(db: AsyncSession, run: Run) -> dict | None:
    """Apply the investigation's recorded try push, if allowed; report its job.

    The push goes through the ordinary recorded-action path: the try-server
    handler, with the API's Lando credentials, auditable as a run action. It is
    held for manual apply when the run requires review. Returns None while the
    push has not been applied.
    """
    rows = await actions_applier.ensure_action_rows(db, run)
    await db.commit()
    pushes = [(row, att) for row, att in rows if row.type == TRY_PUSH_ACTION_TYPE]
    if len(pushes) != 1:
        raise TryPushFailed("The investigation did not record exactly one try push")
    row, attachments = pushes[0]
    if row.status == "pending" and actions_applier.should_auto_apply(run, row.type):
        # The try-server handler never starts other runs, so it needs no client.
        await actions_applier.apply_rows(db, run, [(row, attachments)], None)
        await db.refresh(row)
    if row.status == "failed":
        raise TryPushFailed(f"The try push failed: {row.error}")
    if row.status != "applied":
        return None
    job_id = row.result["job_id"]
    return {
        "job_id": job_id,
        "status_url": f"{try_server_handler._client().base_url}/landing_jobs/{job_id}/",
        "try_url": row.result.get("url"),
    }


def _settled(tasks: list[dict]) -> bool:
    """Whether a task group can make no further progress.

    Tasks that depend, directly or not, on a failed task stay ``unscheduled``
    until their deadline; they count as settled so a broken build is reported
    promptly. Dependencies outside the group (docker images, toolchains from
    the index) are assumed complete.
    """
    state = {t["status"]["taskId"]: t["status"]["state"] for t in tasks}
    deps = {t["status"]["taskId"]: t["task"].get("dependencies", []) for t in tasks}
    blocked: dict[str, bool] = {}

    def is_blocked(task_id: str, seen: frozenset = frozenset()) -> bool:
        if task_id not in blocked:
            blocked[task_id] = state.get(task_id) == "unscheduled" and any(
                state.get(dep) in {"failed", "exception"}
                or (
                    dep not in seen
                    and state.get(dep) == "unscheduled"
                    and is_blocked(dep, seen | {task_id})
                )
                for dep in deps.get(task_id, [])
            )
        return blocked[task_id]

    return all(
        state[t] in {"completed", "failed", "exception"} or is_blocked(t) for t in state
    )


async def ready(watch: dict, http: httpx.AsyncClient) -> bool:
    """Resolve a Lando receipt and check a whole task group, with bounded reads."""

    async def get(url, **params):
        response = await http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    if not watch.get("revision"):
        landed = await get(watch["status_url"])
        if str(landed.get("status", "")).lower() in {
            "failed",
            "cancelled",
            "canceled",
            "aborted",
        }:
            return True  # Let the validator record the Lando failure.
        watch["revision"] = landed.get("commit_id")
    revision = watch.get("revision")
    if not revision:
        return False
    if not watch.get("task_group_id"):
        pushes = await get(f"{TREEHERDER}/api/project/try/push/", revision=revision)
        matching = [p for p in pushes["results"] if p["revision"] == revision]
        if len(matching) != 1:
            return False
        listed = await get(
            f"{TREEHERDER}/api/project/try/jobs/", push_id=matching[0]["id"], count=1
        )
        if not listed["results"]:
            return False
        task = await get(f"{QUEUE}/task/{listed['results'][0]['task_id']}")
        watch["task_group_id"] = task["taskGroupId"]
    token = None
    tasks: list[dict] = []
    for _ in range(50):
        page = await get(
            f"{QUEUE}/task-group/{watch['task_group_id']}/list",
            **({"continuationToken": token} if token else {}),
        )
        tasks.extend(page.get("tasks", []))
        if any(t["status"]["state"] in {"pending", "running"} for t in tasks):
            return False
        token = page.get("continuationToken")
        if not token:
            return bool(tasks) and _settled(tasks)
    raise ValueError("CI task group listing exceeds safety bound")


async def resume(db: AsyncSession, run: Run, *, from_event: bool = False) -> None:
    """Check CI for a waiting run and launch at most one validation execution.

    Network reads happen without a row lock or an open transaction; the launch
    is then claimed under a lock, re-checking that nothing else advanced the
    run. Pulse events may check more often than the sweep, but never more than
    every EVENT_MIN_INTERVAL seconds, and never during a backoff.
    """
    current = await db.get(Run, run.run_id, populate_existing=True)
    if (
        current is None
        or current.finalized_at is not None
        or current.status != RunStatus.awaiting_ci.value
    ):
        await db.commit()
        return
    watch = dict(current.ci_wait or {})
    now = time.time()
    if (
        now < watch.get("not_before", 0)
        or (not from_event and now < watch.get("next_check", 0))
        or (from_event and now < watch.get("last_check", 0) + EVENT_MIN_INTERVAL)
    ):
        await db.commit()
        return
    claim = (watch.get("validation_id"), watch.get("last_check"))
    if not watch.get("status_url"):
        try:
            push = await _try_push(db, current)
        except TryPushFailed as exc:
            await _fail(db, current, str(exc))
            return
        if push is None:
            if now >= watch["deadline"]:
                await _fail(
                    db, current, "The try push was not applied before the CI deadline"
                )
            else:
                await db.commit()  # Held for review: the sweep checks again.
            return
        watch.update(push)
    await db.commit()  # Release the connection while reading CI state.
    try:
        async with (
            asyncio.timeout(30),
            httpx.AsyncClient(timeout=15, follow_redirects=False) as http,
        ):
            should_validate = now >= watch["deadline"] or await ready(watch, http)
    except httpx.HTTPError, ValueError, KeyError, TimeoutError:
        log.warning("Could not reconcile CI for run %s", run.run_id, exc_info=True)
        should_validate = False

    result = await db.execute(
        select(Run)
        .where(Run.run_id == run.run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = result.scalar_one()
    stored = dict(run.ci_wait or {})
    if (
        run.finalized_at is not None
        or run.status != RunStatus.awaiting_ci.value
        or (stored.get("validation_id"), stored.get("last_check")) != claim
    ):
        await db.commit()  # Another event or the sweep got here first.
        return
    stored.update(
        {
            k: watch[k]
            for k in ("job_id", "status_url", "try_url", "revision", "task_group_id")
            if watch.get(k)
        },
        last_check=now,
    )
    if not should_validate:
        run.ci_wait = {**stored, "next_check": now + 60}
        await db.commit()
        return
    validation_id = uuid.uuid4().hex
    artifact_prefix = f"_validation/{validation_id}/"
    try:
        # Fresh, short-lived grants; the original ones may have expired.
        state_url = await gcs.generate_artifact_download_url(
            str(run.run_id),
            "investigation/continuation.json",
            expiration_seconds=VALIDATION_SECONDS,
        )
        patch_url = await gcs.generate_artifact_download_url(
            str(run.run_id),
            "attempts/1/candidate.patch",
            expiration_seconds=VALIDATION_SECONDS,
        )
        if not state_url or not patch_url:
            raise ValueError("Missing saved performance evidence or candidate patch")
        policy = await gcs.generate_results_policy(
            str(run.run_id),
            timeout_seconds=VALIDATION_SECONDS,
            artifact_prefix=artifact_prefix,
        )
        env = {
            **model_to_env(
                AGENT_REGISTRY[run.agent].input_schema.model_validate(run.inputs)
            ),
            "RUN_ID": str(run.run_id),
            "RESULTS_BUCKET": settings.results_bucket,
            "RESULTS_PREFIX": run.results_prefix + artifact_prefix,
            "RESULTS_POLICY_URL": policy["url"],
            "RESULTS_POLICY_FIELDS": json.dumps(policy["fields"]),
            "PERF_VALIDATION_STATE_URL": state_url,
            "PERF_VALIDATION_PATCH_URL": patch_url,
            "PERF_VALIDATION_ID": validation_id,
            "PERF_VALIDATION_STATUS_URL": stored["status_url"],
            "PERF_VALIDATION_TRY_URL": stored.get("try_url") or "",
        }
    except Exception as exc:
        log.exception("Could not prepare validation for run %s", run.run_id)
        await _launch_failed(db, run, stored, now, f"{type(exc).__name__}: {exc}")
        return
    # Persist the launch claim before the remote call. A lost response can leave
    # a worker running, so its grant only permits writes to its private prefix.
    # The finalizer publishes outputs only from the tracked successful attempt.
    run.status = RunStatus.running.value
    run.execution_name = None
    run.ci_wait = {
        **stored,
        "validation_id": validation_id,
        "artifact_prefix": artifact_prefix,
        "launching_until": now + 120,
    }
    await db.commit()
    try:
        execution_name = await jobs.trigger_execution(
            AGENT_REGISTRY[run.agent].job_name, env, timeout_seconds=VALIDATION_SECONDS
        )
    except Exception as exc:
        log.exception("Could not launch validation for run %s", run.run_id)
        result = await db.execute(
            select(Run)
            .where(Run.run_id == run.run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        run = result.scalar_one()
        if (
            run.finalized_at is None
            and run.execution_name is None
            and (run.ci_wait or {}).get("validation_id") == validation_id
        ):
            await _launch_failed(db, run, stored, now, f"{type(exc).__name__}: {exc}")
        else:
            await db.commit()
        return
    result = await db.execute(
        select(Run)
        .where(Run.run_id == run.run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = result.scalar_one()
    # The claim may have expired and been handed back while launching.
    if (
        run.finalized_at is None
        and (run.ci_wait or {}).get("validation_id") == validation_id
    ):
        run.execution_name = execution_name
    else:
        log.warning(
            "Validation %s for run %s started after its claim was released",
            execution_name,
            run.run_id,
        )
    await db.commit()


async def validation_ended(
    db: AsyncSession, run: Run, error: str, *, launched: bool = True
) -> None:
    """Hand a run whose validation did not finish back to waiting, with backoff.

    The investigation's saved state is intact, so a crashed, timed-out or
    never-registered validator is retried rather than failing the run.
    """
    watch = dict(run.ci_wait or {})
    if not launched:
        await _launch_failed(db, run, watch, time.time(), error)
        return
    failures = watch.get("validation_failures", 0) + 1
    if failures >= MAX_VALIDATION_FAILURES:
        await _fail(db, run, f"Performance validation kept failing: {error}")
        return
    run.status = RunStatus.awaiting_ci.value
    run.execution_name = None
    run.ci_wait = {
        **watch,
        "validation_id": None,
        "launching_until": 0,
        "validation_failures": failures,
        "last_validation_error": error,
        "not_before": time.time() + backoff(failures),
    }
    await db.commit()


async def _launch_failed(
    db: AsyncSession, run: Run, watch: dict, now: float, error: str
) -> None:
    """Retry a failed launch with backoff instead of losing the investigation."""
    failures = watch.get("launch_failures", 0) + 1
    if (
        failures >= MAX_LAUNCH_FAILURES
        or now > watch["deadline"] + DEADLINE_GRACE_SECONDS
    ):
        await _fail(db, run, f"Could not launch performance validation: {error}")
        return
    run.status = RunStatus.awaiting_ci.value
    run.execution_name = None
    run.ci_wait = {
        **watch,
        "validation_id": None,
        "launch_failures": failures,
        "last_launch_error": error,
        "not_before": now + backoff(failures),
    }
    await db.commit()


def backoff(attempt: int) -> int:
    return min(MAX_BACKOFF_SECONDS, 300 * 2 ** (attempt - 1))


async def _fail(db: AsyncSession, run: Run, error: str) -> None:
    run.status = RunStatus.failed.value
    run.error = error
    run.execution_name = None
    run.finalized_at = datetime.now(timezone.utc)
    await db.commit()
    await pubsub.publish_run_completed(str(run.run_id), run.agent, run.status)
    # Not reached through agent-run-finished, which notifies everyone else.
    if run.requested_by:
        try:
            await notifications.notify_requester(run)
        except Exception:
            log.exception("Could not notify the requester of run %s", run.run_id)
