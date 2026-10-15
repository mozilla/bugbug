"""Apply actions that wait on a try push, once the push has finished.

An agent can record an action whose params carry ``wait_for_try``, for example a
Phabricator revision that should only go up once CI has checked the fix. It starts
``waiting`` (see ``actions_applier``), and a periodic sweep, ``check_try_pushes``,
looks each one's try push up on Treeherder. Once every selected job has finished:

* it posts the per-job results to Slack, in the thread of the run's announcement;
* if at most ``max_failure_rate`` of the runs failed it applies the action and
  posts the outcome; otherwise it marks the action ``skipped`` and posts why.

``wait_for_try`` is ``{"ref": <try push ref>, "max_failure_rate": <0..1>,
"slack_channel": <channel>, "slack_ref": <ref of the announcement message>}``.

Lando reports no push status, so the push is found on Treeherder by its Lando
commit id, the try push action's ``job_id``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions_applier import SKIPPED, WAITING, _dispatch, resolve_placeholders
from app.database.models import Run, RunAction

log = logging.getLogger(__name__)

TREEHERDER_URL = "https://treeherder.mozilla.org"
_HEADERS = {"User-Agent": "hackbot-api"}
_TIMEOUT_SECONDS = 60

# A push that has not finished a day after it was made is reported and dropped.
MAX_WAIT = timedelta(hours=24)

PASSED = frozenset({"success"})
FAILED = frozenset({"testfailed", "busted", "exception"})
# Anything else ("retry", "usercancel", ...) was superseded or cancelled, so it is
# not counted either way.


@dataclass
class TaskTally:
    label: str
    passed: int = 0
    failed: int = 0
    running: int = 0

    @property
    def counted(self) -> int:
        return self.passed + self.failed


@dataclass
class TryOutcome:
    tallies: list[TaskTally]

    @property
    def complete(self) -> bool:
        return all(t.running == 0 and t.counted > 0 for t in self.tallies)

    @property
    def failed(self) -> int:
        return sum(t.failed for t in self.tallies)

    @property
    def total(self) -> int:
        return sum(t.counted for t in self.tallies)

    @property
    def failure_rate(self) -> float:
        return self.failed / self.total if self.total else 1.0


def tally(jobs: list[dict], labels: list[str]) -> TryOutcome:
    """Count each selected task's runs; a rebuild is several jobs with one label."""
    tallies = {label: TaskTally(label) for label in labels}
    for job in jobs:
        task = tallies.get(job.get("job_type_name"))
        if task is None:
            continue
        if job.get("state") != "completed":
            task.running += 1
        elif job.get("result") in PASSED:
            task.passed += 1
        elif job.get("result") in FAILED:
            task.failed += 1
    return TryOutcome(list(tallies.values()))


async def fetch_try_jobs(lando_job_id: Any) -> list[dict] | None:
    """The jobs of the try push Lando made for ``lando_job_id``.

    ``None`` until Treeherder knows the push: Lando lands it asynchronously.
    """
    async with httpx.AsyncClient(
        base_url=TREEHERDER_URL, headers=_HEADERS, timeout=_TIMEOUT_SECONDS
    ) as client:
        pushes = await client.get(
            "/api/project/try/push/", params={"lando_commit_id": lando_job_id}
        )
        pushes.raise_for_status()
        results = pushes.json().get("results") or []
        if not results:
            return None
        jobs = await client.get(
            "/api/project/try/jobs/",
            params={"push_id": results[0]["id"], "count": 2000},
        )
        jobs.raise_for_status()
        return jobs.json().get("results") or []


@dataclass
class TryPushSweepResult:
    considered: int = 0
    still_running: int = 0
    applied: int = 0
    not_applied: int = 0
    failed: int = 0


def _results_text(outcome: TryOutcome, try_url: str | None) -> str:
    push = f"<{try_url}|try push>" if try_url else "try push"
    lines = [f"Try jobs completed ({push}):"]
    for t in outcome.tallies:
        lines.append(f"• `{t.label}`: {t.passed}/{t.counted} passed, {t.failed} failed")
    lines.append(
        f"Overall: {outcome.failed}/{outcome.total} failed ({outcome.failure_rate:.0%})"
    )
    return "\n".join(lines)


def _results_plan(outcome: TryOutcome, try_url: str | None) -> str:
    lines = [f"Try push: {try_url}" if try_url else "Try push:"]
    for t in outcome.tallies:
        lines.append(f"- {t.label}: {t.passed}/{t.counted} passed")
    return "\n".join(lines)


class _Sweep:
    def __init__(self, db: AsyncSession, now: datetime):
        self.db = db
        self.now = now
        self.result = TryPushSweepResult()

    async def check(self, row: RunAction) -> None:
        self.result.considered += 1
        cond = row.params["wait_for_try"]
        run = await self.db.get(Run, row.run_id)
        siblings = (
            (
                await self.db.execute(
                    select(RunAction).where(RunAction.run_id == row.run_id)
                )
            )
            .scalars()
            .all()
        )
        by_ref = {s.ref: s for s in siblings if s.ref}
        try_row = by_ref.get(cond["ref"])
        announce = by_ref.get(cond.get("slack_ref"))

        if try_row is None or try_row.status == "failed":
            error = try_row.error if try_row is not None else "no try push recorded"
            await self._give_up(
                run, row, announce, cond, f"the try push failed: {error}"
            )
            return
        if try_row.status != "applied":
            # Not pushed yet (awaiting a manual apply); give it the same day.
            if self.now - run.created_at > MAX_WAIT:
                await self._give_up(
                    run, row, announce, cond, "the try push was never made"
                )
            else:
                self.result.still_running += 1
            return

        try_url = (try_row.result or {}).get("url")
        labels = (try_row.result or {}).get("tasks") or []
        jobs = await fetch_try_jobs(try_row.result["job_id"])
        outcome = tally(jobs or [], labels)
        if jobs is None or not outcome.complete:
            pushed_at = try_row.applied_at or run.created_at
            if self.now - pushed_at > MAX_WAIT:
                await self._give_up(
                    run,
                    row,
                    announce,
                    cond,
                    f"the <{try_url}|try push> did not finish within "
                    f"{MAX_WAIT.total_seconds() / 3600:.0f} hours",
                )
            else:
                self.result.still_running += 1
            return

        await self._post(run, announce, cond, _results_text(outcome, try_url))

        limit = cond["max_failure_rate"]
        verdict = (
            f"{outcome.failed}/{outcome.total} failed ({outcome.failure_rate:.0%})"
        )
        if outcome.failure_rate > limit:
            row.status = SKIPPED
            row.result = {"failed": outcome.failed, "total": outcome.total}
            row.error = f"{verdict}, above the {limit:.0%} limit"
            await self.db.commit()
            self.result.not_applied += 1
            await self._post(
                run,
                announce,
                cond,
                f":x: {verdict}, above the {limit:.0%} limit: the fix did not hold, "
                "so no revision was opened.",
                broadcast=True,
            )
            return

        results_by_ref = {
            s.ref: s.result
            for s in siblings
            if s.ref and s.status == "applied" and s.result is not None
        }
        params = {k: v for k, v in row.params.items() if k != "wait_for_try"}
        params = resolve_placeholders(params, results_by_ref)
        plan = _results_plan(outcome, try_url)
        params["test_plan"] = (
            f"{params['test_plan']}\n\n{plan}" if params.get("test_plan") else plan
        )
        applied = await _dispatch(run, row.type, params, [])
        row.status = applied.status
        row.result = applied.result
        row.error = applied.error
        if applied.status == "applied":
            row.applied_at = self.now
        await self.db.commit()

        if applied.status == "applied":
            self.result.applied += 1
            link = (applied.result or {}).get("url")
            target = f"<{link}|the revision>" if link else "the revision"
            reviewers = ", ".join(params.get("reviewers") or [])
            review = f" for review by {reviewers}" if reviewers else ""
            text = f":white_check_mark: {verdict}: opened {target}{review}."
        else:
            self.result.failed += 1
            text = (
                f":warning: {verdict}, within the {limit:.0%} limit, but opening the "
                f"revision failed: {applied.error}"
            )
        await self._post(run, announce, cond, text, broadcast=True)

    async def _give_up(self, run, row, announce, cond, reason: str) -> None:
        row.status = SKIPPED
        row.error = reason
        await self.db.commit()
        self.result.not_applied += 1
        await self._post(
            run, announce, cond, f":x: No revision: {reason}.", broadcast=True
        )

    async def _post(self, run, announce, cond, text: str, broadcast=False) -> None:
        channel = cond.get("slack_channel")
        if announce is not None:
            channel = announce.params.get("channel", channel)
        if not channel:
            return
        params: dict[str, Any] = {"channel": channel, "text": text}
        ts = (announce.result or {}).get("ts") if announce is not None else None
        if ts:
            params["thread_ts"] = ts
            params["reply_broadcast"] = broadcast
        posted = await _dispatch(run, "slack.post_message", params, [])
        if posted.status != "applied":
            log.warning("Slack update for run %s failed: %s", run.run_id, posted.error)


async def check_try_pushes(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> TryPushSweepResult:
    """Check every action waiting on a try push, oldest first."""
    sweep = _Sweep(db, now or datetime.now(timezone.utc))
    rows = (
        (
            await db.execute(
                select(RunAction)
                .where(RunAction.status == WAITING)
                .order_by(RunAction.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        try:
            await sweep.check(row)
        except Exception:
            # One push Treeherder cannot answer for must not stop the others.
            log.exception("Could not check the try push for action %s", row.id)
            await db.rollback()
            sweep.result.failed += 1
    return sweep.result
