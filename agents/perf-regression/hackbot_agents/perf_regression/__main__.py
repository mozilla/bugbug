# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Hackbot entry point: evidence, isolated candidate trees, try, dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from hackbot_runtime import HackbotAgentResult, HackbotContext, changes, run_async
from hackbot_runtime.actions import try_server
from lando_client import LandoClient, encode_patch, try_task_config_patch
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import propose
from .ci import REPETITIONS, TryClient
from .evidence import EvidenceClient, EvidenceError, EvidencePending, OutOfScope
from .policy import forbidden, patch_paths
from .preflight import check_candidate
from .workflow import finish_attempt, format_report, run_attempts

log = logging.getLogger(__name__)
# A validation execution waits this long for Perfherder ingestion once CI has
# settled, instead of handing back to the API and being relaunched.
VALIDATION_POLL_SECONDS = 15 * 60
VALIDATION_SETTLE_SECONDS = 10 * 60


class Inputs(BaseSettings):
    bug_id: int = Field(gt=0)
    alert_id: int | None = Field(default=None, gt=0)
    max_attempts: int = Field(default=1, ge=1, le=1)
    model: str | None = None
    max_turns: int = Field(default=500, ge=1, le=500)
    effort: str = "high"
    ci_timeout_seconds: int = Field(default=86400, ge=60, le=86400)
    perf_validation_state_url: str | None = None
    perf_validation_patch_url: str | None = None
    perf_validation_id: str | None = None
    # The Lando job and Treeherder link of the try push the API applied.
    perf_validation_status_url: str | None = None
    perf_validation_try_url: str | None = None
    # Only investigate these suites (e.g. ["speedometer3"]); all when unset.
    suites: list[str] | None = None
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


class RegressionResult(HackbotAgentResult):
    bug_id: int
    alert_id: int | None = None
    status: str
    summary: str
    result: str
    attempts: list[dict] = Field(default_factory=list)
    positive_attempts: list[int] = Field(default_factory=list)
    winning_attempt: int | None = None
    verified_patch_url: str | None = None
    evidence_url: str | None = None
    ci_pending: dict | None = None
    validation_id: str | None = None


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def validate_candidate(
    repo: Path, base: str, *, harness_changes_allowed: bool = False
) -> None:
    """Check the uncommitted candidate before it is committed."""
    if changes.base_commit(repo) != base:
        raise EvidenceError(
            "Candidate changed HEAD; candidates must edit the pinned base without committing"
        )
    paths = set(
        git(repo, "diff", "--no-renames", "--name-only", "-z", "HEAD").split("\0")
    )
    paths.update(
        git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    )
    _reject_forbidden(paths, harness_changes_allowed)


def validate_patch(
    repo: Path, base: str, patch: bytes, *, harness_changes_allowed: bool
) -> None:
    """Check exactly what will be pushed, after anything else may have written."""
    paths = set(
        git(repo, "diff", "--no-renames", "--name-only", "-z", base, "HEAD").split("\0")
    )
    try:
        paths.update(patch_paths(patch))
    except ValueError as exc:
        raise EvidenceError(f"Candidate patch cannot be checked: {exc}") from exc
    _reject_forbidden(paths, harness_changes_allowed)


def _reject_forbidden(paths: set[str], harness_changes_allowed: bool) -> None:
    if any(
        forbidden(path, harness_changes_allowed=harness_changes_allowed)
        for path in paths
        if path
    ):
        raise EvidenceError(
            "Candidate changes benchmark/CI configuration instead of Firefox code"
        )


async def main(ctx: HackbotContext) -> RegressionResult:
    inputs = Inputs()
    if inputs.perf_validation_state_url:
        return await validate_saved(ctx, inputs)
    # Deployed runs release the job while CI runs; the API resumes them. Local
    # runs wait in place and report the verdict before exiting.
    wait_for_ci = not os.environ.get("CLOUD_RUN_JOB")
    scratch = Path(tempfile.mkdtemp(prefix="perf-regression-"))
    evidence_dir = scratch / "evidence"
    progress: dict = {"status": "collecting", "attempts": []}
    totals = {"num_turns": 0, "total_cost_usd": 0.0}
    candidate_patch = scratch / "candidate.patch"
    manifest = None
    baselines = {}
    ci_pending = None
    verified_patch_url = None
    evidence_url = None
    analysis: dict | None = None
    report = scratch / "report.md"

    def checkpoint(value: dict) -> None:
        nonlocal progress
        progress = {**value, "result": format_report(analysis, value)}
        ctx.publish_json("investigation/progress.json", progress)
        report.write_text(progress["result"])
        ctx.publish_file("investigation/report.md", report, "text/markdown")

    def persist(name: str, value: dict) -> None:
        ctx.publish_json(f"investigation/{name}.json", value)

    try:
        async with httpx.AsyncClient(
            timeout=120,
            follow_redirects=True,
            headers={"User-Agent": "Hackbot-Perf-Regression/0.1"},
        ) as http:
            collector = EvidenceClient(http)
            # The creation webhook can arrive before Perfherder attaches the
            # comment/bug number. Retry only that, never a try POST.
            try:
                for retry in range(3):
                    try:
                        manifest = await collector.collect(
                            inputs.bug_id, inputs.alert_id, evidence_dir, inputs.suites
                        )
                        break
                    except EvidencePending:
                        if retry == 2:
                            raise
                        await asyncio.sleep(30)
            except OutOfScope as exc:
                # Not a failure: nothing to investigate, before any checkout,
                # model session or try push.
                result = {
                    "status": "out_of_scope",
                    "summary": str(exc),
                    "attempts": [],
                    "positive_attempts": [],
                }
                checkpoint(result)
                return RegressionResult(
                    bug_id=inputs.bug_id,
                    alert_id=inputs.alert_id,
                    result=progress["result"],
                    **totals,
                    **result,
                )
            evidence_url = ctx.publish_json("investigation/evidence.json", manifest)
            repo = await ctx.prepare_repo(ref=manifest["bad_git"], depth=100)
            base = changes.base_commit(repo)
            if base != manifest["bad_git"]:
                raise EvidenceError("Checkout is not at the regressing revision")
            ci = TryClient(http, manifest, timeout_seconds=inputs.ci_timeout_seconds)

            async def candidate(number: int, previous: list[dict]) -> dict:
                nonlocal analysis
                tree = scratch / f"candidate-{number}"
                git(repo, "worktree", "add", "--detach", str(tree), base)
                candidate_log = scratch / f"candidate-{number}.log"
                usage = {"num_turns": 0, "total_cost_usd": 0.0}
                try:
                    try:
                        proposal = await propose(
                            tree,
                            evidence_dir,
                            previous,
                            model=inputs.model,
                            max_turns=inputs.max_turns,
                            effort=inputs.effort,
                            log=candidate_log,
                            usage=usage,
                        )
                    finally:
                        totals["num_turns"] += usage["num_turns"]
                        totals["total_cost_usd"] += usage["total_cost_usd"]
                    for key in totals:
                        proposal.pop(key)
                    analysis = proposal
                    persist(f"candidate-{number}-analysis", proposal)
                    if not proposal["has_candidate"]:
                        return proposal
                    validate_candidate(
                        tree,
                        base,
                        harness_changes_allowed=manifest["harness_changes_allowed"],
                    )
                    # Commit untracked tests too before computing the complete diff.
                    changes.commit_all(
                        tree,
                        f"Bug {inputs.bug_id} - {proposal['summary'].splitlines()[0][:150]}",
                    )
                    change_set = changes.collect(tree, base, ctx.config.source.repo_url)
                    if change_set is None:
                        raise EvidenceError(
                            "Model proposed a fix but produced no patch"
                        )
                    validate_patch(
                        tree,
                        base,
                        change_set.patch,
                        harness_changes_allowed=manifest["harness_changes_allowed"],
                    )
                    candidate_patch.write_bytes(change_set.patch)
                    proposal["patch_url"] = ctx.publish_file(
                        f"attempts/{number}/candidate.patch",
                        candidate_patch,
                        "text/plain",
                    )
                    preflight_log = scratch / f"candidate-{number}-preflight.log"
                    try:
                        await asyncio.to_thread(
                            check_candidate, tree, base, preflight_log
                        )
                    finally:
                        if preflight_log.exists():
                            ctx.publish_file(
                                f"attempts/{number}/preflight.log",
                                preflight_log,
                                "text/plain",
                            )
                    return proposal
                finally:
                    if candidate_log.exists():
                        ctx.publish_file(
                            f"attempts/{number}/agent.log", candidate_log, "text/plain"
                        )
                    try:
                        subprocess.run(
                            ["profiler-cli", "stop"],
                            capture_output=True,
                            check=False,
                            timeout=30,
                        )
                    except OSError, subprocess.TimeoutExpired:
                        log.warning("Could not stop profiler-cli", exc_info=True)
                    try:
                        git(repo, "worktree", "remove", "--force", str(tree))
                    except subprocess.CalledProcessError:
                        log.warning("Could not remove %s", tree, exc_info=True)
                    # The runtime publishes the main checkout as changes/; only
                    # a recovered candidate may reach it (applied later).
                    git(repo, "reset", "-q", "--hard", base)
                    git(repo, "clean", "-qfd")

            async def historical_baselines() -> tuple[dict, dict]:
                baselines.update(
                    await collector.baselines(manifest["alert"], manifest["metrics"])
                )
                persist("historical-baselines", baselines)
                return baselines["good"], baselines["bad"]

            async def measure(number: int, proposal: dict) -> dict:
                # The try push and changes/ both come from the main checkout.
                git(
                    repo,
                    "-c",
                    "user.name=Hackbot",
                    "-c",
                    "user.email=hackbot@mozilla.tld",
                    "am",
                    str(candidate_patch),
                )
                tasks = manifest["tasks"]
                if not wait_for_ci:
                    # Deployed: hackbot-api applies the push with its own Lando
                    # credentials, then waits for CI and resumes this run.
                    await try_server.push(
                        ctx.actions,
                        reasoning="Measure the candidate fix on the alert's "
                        f"performance tasks, {REPETITIONS} times each",
                        tasks=tasks,
                        rebuild=REPETITIONS,
                        title=f"Bug {inputs.bug_id} - perf-regression candidate",
                    )
                    return {"tasks": tasks, "repetitions": REPETITIONS, "pending": True}
                # Local: there is no API to apply actions, so push with the
                # developer's own Lando token, as `mach try` would.
                submission = await push_to_try(repo, base, tasks)
                persist(f"candidate-{number}-submission", submission)
                state = await ci.wait(
                    submission,
                    lambda state: persist(f"candidate-{number}-ci", state),
                )
                if state.get("revision"):
                    try:
                        state["candidate_url"] = await ci.pushed_candidate(
                            state["revision"]
                        )
                    except httpx.HTTPError, KeyError, IndexError:
                        log.warning(
                            "Could not find the pushed candidate", exc_info=True
                        )
                return state

            result = await run_attempts(
                manifest["metrics"],
                max_attempts=inputs.max_attempts,
                propose=candidate,
                historical_baselines=historical_baselines,
                measure=measure,
                checkpoint=checkpoint,
            )
            result["result"] = progress["result"]
            if result["status"] == "awaiting_ci":
                # The API fills in the Lando job once it applies the push.
                ci_pending = {"deadline": time.time() + inputs.ci_timeout_seconds}
                persist(
                    "continuation",
                    {
                        "version": 1,
                        "run_id": ctx.run_id,
                        "manifest": manifest,
                        "analysis": analysis,
                        "evidence_url": evidence_url,
                        "baselines": baselines,
                        "totals": totals,
                        "result": result,
                        "ci_pending": ci_pending,
                    },
                )
            elif result["status"] == "fixed":
                verified_patch_url = publish_verified(
                    ctx, repo, base, candidate_patch, result["result"]
                )
    except Exception as exc:
        checkpoint(
            {
                "status": "blocked",
                "summary": f"{type(exc).__name__}: {exc}",
                "attempts": progress.get("attempts", []),
                "positive_attempts": [
                    a["number"]
                    for a in progress.get("attempts", [])
                    if a.get("validation", {}).get("positive")
                ],
                **totals,
            }
        )
        # The runtime owns failure summaries and exit codes. Returning a result
        # here would mark a stopped investigation as a successful Hackbot run.
        raise
    finally:
        # Profiles are already public CI artifacts; retain their source URLs in
        # the manifest instead of duplicating multi-GB binaries in the dashboard.
        for path in evidence_dir.glob("*"):
            if (
                path.is_file()
                and (
                    path.name in {"bug.json", "alert.json", "historical-data.json"}
                    or path.name.startswith("culprit-")
                )
                and path.suffix in {".json", ".patch"}
            ):
                ctx.publish_file(
                    f"investigation/{path.relative_to(scratch).as_posix()}",
                    path,
                    "text/plain",
                )
    return RegressionResult(
        bug_id=inputs.bug_id,
        alert_id=manifest["alert_id"] if manifest else inputs.alert_id,
        ci_pending=ci_pending,
        verified_patch_url=verified_patch_url,
        evidence_url=evidence_url,
        **totals,
        **result,
    )


async def push_to_try(repo: Path, base: str, tasks: list[str]) -> dict:
    """Push the main checkout's candidate commit, for local runs only."""
    payload = changes.build_try_push(repo, base)
    if payload is None:
        raise EvidenceError("The candidate has no commit to push to try")
    lando = LandoClient()
    job_id = await lando.submit_try_patches(
        [
            *payload["patches"],
            encode_patch(try_task_config_patch(tasks, rebuild=REPETITIONS)),
        ],
        payload["base_commit"],
        base_commit_vcs=payload["base_commit_vcs"],
        patch_format=payload["patch_format"],
    )
    return {
        "job_id": job_id,
        "url": lando.treeherder_url(job_id),
        "status_url": f"{lando.base_url}/landing_jobs/{job_id}/",
        "tasks": tasks,
        "repetitions": REPETITIONS,
    }


def publish_verified(
    ctx: HackbotContext, repo: Path, base: str, patch: Path, report: str
) -> str:
    """Publish a recovered candidate, applying it first if the checkout lacks it."""
    if changes.base_commit(repo) == base:
        git(
            repo,
            "-c",
            "user.name=Hackbot",
            "-c",
            "user.email=hackbot@mozilla.tld",
            "am",
            str(patch),
        )
    url = ctx.publish_file("verified/fix.patch", patch, "text/plain")
    description = patch.parent / "fix-description.txt"
    description.write_text(report)
    ctx.publish_file("verified/description.txt", description, "text/plain")
    return url


async def validate_saved(ctx: HackbotContext, inputs: Inputs) -> RegressionResult:
    """Finish an investigation once CI is done. No model session or Try push.

    The report links the candidate commit as pushed to Try, which is the
    authoritative copy of what was measured.
    """
    scratch = Path(tempfile.mkdtemp(prefix="perf-validation-"))
    async with httpx.AsyncClient(
        timeout=120,
        follow_redirects=True,
        headers={"User-Agent": "Hackbot-Perf-Regression/0.1"},
    ) as http:

        async def download(source: str) -> bytes:
            if source.startswith(("https://", "http://")):
                response = await http.get(source)
                response.raise_for_status()
                return response.content
            return Path(source).read_bytes()  # Manual local Compose continuation.

        saved = json.loads(await download(inputs.perf_validation_state_url))
        if saved["version"] != 1 or saved["run_id"] != ctx.run_id:
            raise EvidenceError("Saved validation state belongs to another run")
        collector = EvidenceClient(http)
        # The metrics chosen before the push are measured as they were, even
        # if sheriffs re-triage the alert while CI runs.
        manifest = saved["manifest"]
        alert, rows, tasks = manifest["alert"], manifest["metrics"], manifest["tasks"]
        ci = TryClient(http, manifest, settle_seconds=VALIDATION_SETTLE_SECONDS)
        attempt = saved["result"]["attempts"][0]
        if not inputs.perf_validation_status_url:
            raise EvidenceError(
                "Validation needs the Lando job of the applied try push"
            )
        # The API applied the recorded try push and passes its Lando job.
        submission = {
            "status_url": inputs.perf_validation_status_url,
            "url": inputs.perf_validation_try_url,
            "tasks": tasks,
            "repetitions": REPETITIONS,
        }
        attempt["ci"] = {**attempt.get("ci", {}), **submission}
        previous = saved.get("ci_state", {})
        poll_until = time.monotonic() + VALIDATION_POLL_SECONDS
        while True:
            try:
                measured = await ci.check(submission, previous)
            except httpx.HTTPError as exc:
                measured = {
                    **previous,
                    "complete": False,
                    "measurements": previous.get("measurements", {}),
                    "last_error": str(exc),
                }
            done = measured["complete"] or measured.get("failure_reason")
            # Keep waiting here only for Perfherder ingestion; if CI itself is
            # still running, hand back to the API instead of holding a job.
            if done or not measured.get("settled_at") or time.monotonic() > poll_until:
                break
            previous = measured
            await asyncio.sleep(60)
        if (
            not measured["complete"]
            and not measured.get("failure_reason")
            and time.time() >= saved["ci_pending"]["deadline"]
        ):
            measured["failure_reason"] = (
                "CI timed out before all selected tasks and metrics completed"
            )
        ctx.publish_json("investigation/candidate-1-ci.json", measured)
        verified = None
        if measured["complete"] or measured.get("failure_reason"):
            if measured.get("revision"):
                try:
                    measured["candidate_url"] = await ci.pushed_candidate(
                        measured["revision"]
                    )
                except httpx.HTTPError, KeyError, IndexError:
                    log.warning("Could not find the pushed candidate", exc_info=True)
            baselines = saved.get("baselines") or await collector.baselines(alert, rows)
            result = finish_attempt(
                rows, baselines["good"], baselines["bad"], attempt, measured
            )
            ci_pending = None
        else:
            result = saved["result"]
            saved["ci_state"] = measured
            ctx.publish_json("investigation/continuation.json", saved)
            ci_pending = saved["ci_pending"]
        result["result"] = format_report(saved["analysis"], result)
        ctx.publish_json("investigation/progress.json", result)
        report = scratch / "report.md"
        report.write_text(result["result"])
        ctx.publish_file("investigation/report.md", report, "text/markdown")
        if result["status"] == "fixed":
            patch = scratch / "candidate.patch"
            patch.write_bytes(await download(inputs.perf_validation_patch_url))
            repo = await ctx.prepare_repo(ref=manifest["bad_git"], depth=100)
            if changes.base_commit(repo) != manifest["bad_git"]:
                raise EvidenceError(
                    "Validation checkout is not at the regressing revision"
                )
            verified = publish_verified(
                ctx, repo, manifest["bad_git"], patch, result["result"]
            )
        return RegressionResult(
            bug_id=inputs.bug_id,
            alert_id=manifest["alert_id"],
            ci_pending=ci_pending,
            validation_id=inputs.perf_validation_id,
            evidence_url=saved.get("evidence_url"),
            verified_patch_url=verified,
            **saved["totals"],
            **result,
        )


def run() -> None:
    run_async(main)


if __name__ == "__main__":
    run()
