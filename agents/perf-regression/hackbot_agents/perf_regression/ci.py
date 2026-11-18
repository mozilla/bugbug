# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Follow one candidate try push until it has complete, matching CI data."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable

import httpx

from .evidence import TREEHERDER, EvidenceClient, EvidenceError, metric_key

TRY_HG = "https://hg.mozilla.org/try"
# Every selected task runs this many times (rebuild: 5).
REPETITIONS = 5


def is_lint_job(job: dict) -> bool:
    name = job.get("job_type_name", "")
    return name.startswith("source-test-mozlint-")


# Potential prerequisites. A push can also contain unrelated jobs of these types;
# their failures only block validation when selected repetitions never ran.
PREREQUISITE_PREFIXES = (
    "Gecko Decision Task",
    "build-",
    "docker-image-",
    "fetch-",
    "signing-",
    "toolchain-",
)


class TryClient:
    """Read the CI state of one candidate Try push from Lando and Treeherder."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        manifest: dict,
        *,
        timeout_seconds: int = 24 * 60 * 60,
        poll_seconds: float = 600,
        settle_seconds: float = 3600,
    ):
        self.client = client
        self.evidence = EvidenceClient(client)
        self.manifest = manifest
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.settle_seconds = settle_seconds

    async def pushed_candidate(self, revision: str) -> str | None:
        """The candidate commit as it was pushed to Try, for the report.

        Lando pushes the candidate and then the try configuration, so the
        candidate is the second-newest commit of the push.
        """
        pushes = await self.evidence.get(
            f"{TREEHERDER}/api/project/try/push/", revision=revision
        )
        matching = [p for p in pushes["results"] if p["revision"] == revision]
        commits = [r["revision"] for r in matching[0]["revisions"]] if matching else []
        return f"{TRY_HG}/rev/{commits[1]}" if len(commits) >= 2 else None

    async def check(
        self,
        submission: dict,
        previous: dict | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> dict:
        """One CI snapshot, usable across executions without keeping a job alive.

        Persist wall-clock settlement time with the snapshot so the ingestion
        grace period survives a restart. The local wait loop supplies its clock.
        """
        previous = previous or {}
        revision = previous.get("revision")
        push_id = previous.get("push_id")
        settled_at = previous.get("settled_at")
        if not revision:
            status = await self.evidence.get(submission["status_url"])
            revision = status.get("commit_id")
            if str(status.get("status", "")).lower() in {
                "failed",
                "cancelled",
                "canceled",
                "aborted",
            }:
                raise EvidenceError(
                    f"Lando did not land the push: {status.get('status')}"
                )
        if revision and push_id is None:
            pushes = await self.evidence.get(
                f"{TREEHERDER}/api/project/try/push/", revision=revision
            )
            matching = [p for p in pushes["results"] if p["revision"] == revision]
            if len(matching) == 1:
                push_id = matching[0]["id"]
        if push_id is not None:
            data = await self.evidence.get(
                f"{TREEHERDER}/api/project/try/jobs/",
                push_id=push_id,
                count=2000,
            )
            jobs = data["results"]
            if len(jobs) >= 2000:
                raise EvidenceError(
                    "Try job list is truncated; cannot verify completeness"
                )
            tasks = set(submission["tasks"])
            selected = [j for j in jobs if j["job_type_name"] in tasks]
            prerequisites = [
                j for j in jobs if j["job_type_name"].startswith(PREREQUISITE_PREFIXES)
            ]
            ignored_lint = [
                j
                for j in jobs
                if is_lint_job(j)
                and j["state"] == "completed"
                and j["result"] != "success"
            ]
            failed = [
                j
                for j in [*selected, *prerequisites]
                if j["state"] == "completed"
                and j["result"] not in {"success", "retry", "superseded"}
            ]
            decision_done = any(
                j["job_type_name"] == "Gecko Decision Task"
                and j["state"] == "completed"
                for j in jobs
            )
            counts = Counter(
                j["job_type_name"]
                for j in selected
                if j["state"] == "completed" and j["result"] == "success"
            )
            successful_ids = {
                j["id"]
                for j in selected
                if j["state"] == "completed" and j["result"] == "success"
            }
            # Preserve available measurements even when another selected
            # test or its build fails. Unrelated test failures do not veto
            # a completed set of selected performance jobs.
            points = await self.evidence.measurements(
                "try", push_id, self.manifest["metrics"]
            )
            points = {
                key: [
                    p
                    for p in group
                    if p["job_id"] in successful_ids and p["revision"] == revision
                ]
                for key, group in points.items()
            }
            repetitions = submission["repetitions"]
            selected_finished = all(j["state"] == "completed" for j in selected)
            prerequisites_finished = all(
                j["state"] == "completed" for j in prerequisites
            )
            finished_counts = Counter(
                j["job_type_name"] for j in selected if j["state"] == "completed"
            )
            repetitions_finished = all(finished_counts[t] >= repetitions for t in tasks)
            if repetitions_finished:
                # These tests ran, so other builds/toolchains cannot have
                # blocked them. Perfherder may still be ingesting data.
                failed = [j for j in failed if j["job_type_name"] in tasks]
            complete = (
                all(counts[t] >= repetitions for t in tasks)
                and selected_finished
                and not failed
                and all(
                    len(points.get(metric_key(r["series_signature"]), []))
                    >= repetitions
                    for r in self.manifest["metrics"]
                )
            )
            stopped = (
                bool(failed)
                and selected_finished
                and (
                    repetitions_finished
                    or (
                        prerequisites_finished
                        and any(j["job_type_name"] not in tasks for j in failed)
                    )
                )
            )
            now = clock()
            # Start the ingestion grace period only after tests and
            # potential prerequisites finish; reset it if CI resumes.
            settled = (
                selected_finished
                and prerequisites_finished
                and (bool(selected) or decision_done)
            )
            if not settled or complete or stopped:
                settled_at = None
            elif settled_at is None:
                settled_at = now
            incomplete = (
                settled_at is not None and now - settled_at >= self.settle_seconds
            )
            state = {
                "revision": revision,
                "push_id": push_id,
                "job_counts": dict(counts),
                "ignored_lint_failures": ignored_lint,
                "url": f"{TREEHERDER}/jobs?repo=try&revision={revision}",
                "complete": complete,
                "measurements": points,
            }
            if stopped:
                state.update(failure_reason="CI build/test failure", failed_jobs=failed)
            elif incomplete:
                state.update(
                    failure_reason="CI finished without every selected "
                    "task and metric reporting the required repetitions"
                )
            state["settled_at"] = settled_at
            return state
        return {
            "revision": revision,
            "push_id": push_id,
            "complete": False,
            "measurements": {},
            "url": submission.get("url"),
        }

    async def wait(self, submission: dict, progress: Callable[[dict], None]) -> dict:
        """Explicit local-only polling; deployed runs save state and exit."""
        deadline = time.monotonic() + self.timeout_seconds
        state = None
        last_error = None
        while time.monotonic() < deadline:
            try:
                state = await self.check(submission, state, clock=time.monotonic)
                if state.get("push_id") is not None:
                    progress(state)
                if state["complete"] or state.get("failure_reason"):
                    return state
            except httpx.HTTPError as exc:
                last_error = str(exc)
            await asyncio.sleep(
                min(self.poll_seconds, max(0, deadline - time.monotonic()))
            )
        reason = (
            "CI timed out before all selected tasks and metrics completed. "
            f"Last HTTP error: {last_error}"
        )
        if state is None or state.get("push_id") is None:
            raise EvidenceError(reason)
        return {**state, "complete": False, "failure_reason": reason}
