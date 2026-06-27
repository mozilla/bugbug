# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Build-repair evaluation harness.

Runs the ported hackbot build-repair agent (``run_build_repair``) over a Weave
dataset of Firefox build failures, then scores its output: deterministic build
verification plus an LLM-as-a-judge comparison to the landed fix.

Usage:
    python -m evals.eval --no-try-push --limit 1
    python -m evals.eval --trials 3 --parallelism 8
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import tempfile
import uuid
from functools import cached_property
from pathlib import Path

import bugsy
import weave
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from agent_tools.firefox.tools.build_firefox import build_firefox
from hackbot_agents.build_repair.agent import run_build_repair
from hackbot_agents.build_repair.config import ANALYSIS_MODEL, FIX_MODEL

from .scorer import (
    BasicMetricsScorer,
    BuildPassRateScorer,
    LLMFixMatchingScorer,
)
from .verify import VERIFY_MODEL, GroundTruth, is_data_contaminated, run_verify
from .worktree import WorktreeManager

logger = logging.getLogger(__name__)


def _collect_diff(worktree_path: Path, base_commit: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=worktree_path, capture_output=True)
    result = subprocess.run(
        ["git", "diff", "--staged", base_commit],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _bugzilla_server():
    """Bugzilla MCP server for the agent.

    Prefer the broker (``BUGZILLA_MCP_URL``) so the eval container holds no
    Bugzilla credentials -- same isolation as production. Falls back to an
    in-process server for local runs without a broker.
    """
    mcp_url = os.environ.get("BUGZILLA_MCP_URL")
    if mcp_url:
        return {"type": "http", "url": mcp_url}
    client = bugsy.Bugsy(
        bugzilla_url=os.environ.get(
            "BUGZILLA_API_URL", "https://bugzilla.mozilla.org/rest"
        ),
        api_key=os.environ.get("BUGZILLA_API_KEY"),
    )
    return build_sdk_server("bugzilla", BugzillaContext(client=client), bugzilla.TOOLS)


class BuildRepairModel(weave.Model):
    """Weave Model: one worktree per example, runs the ported build-repair agent."""

    firefox_repo: str
    no_try_push: bool = False
    judge_model: str = VERIFY_MODEL

    @cached_property
    def worktree_mgr(self) -> WorktreeManager:
        return WorktreeManager(self.firefox_repo)

    @weave.op()
    async def invoke(
        self,
        bug_id: int,
        # Bug fields before the fix. This filed is a part of the dataset.
        # The new Hackbot agent is not using it. It pulls the Bugzilla bug itself.
        # TODO: investigate how to hide the fix in evals for the new agent
        pre_fix_bug: dict,
        gh_failure_commits: list[str],
        gh_fix_commits: list[str],
        failures: list[dict],
        fix_commit_date: str,
        **kwargs,
    ) -> dict:
        if is_data_contaminated(fix_commit_date, ANALYSIS_MODEL, FIX_MODEL):
            logger.warning(
                "Skipping bug %s: fix date %s precedes model cutoff",
                bug_id,
                fix_commit_date,
            )
            raise ValueError("skipped_data_contamination")

        failure_commit = gh_failure_commits[0]
        wt_name = f"bug-{bug_id}-{uuid.uuid4().hex[:8]}"
        worktree_path = self.worktree_mgr.create(failure_commit, wt_name)
        try:
            fx_ctx = FirefoxContext.from_source_repo(worktree_path)
            result = await run_build_repair(
                bugzilla_mcp_server=_bugzilla_server(),
                source_repo=worktree_path,
                fx_ctx=fx_ctx,
                bug_id=bug_id,
                git_commit=failure_commit,
                failure_tasks={f["task_name"]: f["task_id"] for f in failures},
                run_try_push=not self.no_try_push,
            )

            diff = _collect_diff(worktree_path, failure_commit)
            output: dict = {
                "error": None,
                "diff": diff,
                "cost_usd": result.total_cost_usd or 0.0,
                "num_turns": result.num_turns,
                "local_build_passed": None,
                "try_build_passed": result.try_build_passed,
            }

            if diff.strip():
                build_result = await build_firefox(
                    worktree_path, fx_ctx.mozconfig, fx_ctx.objdir
                )
                output["local_build_passed"] = build_result["success"]

            scratch_out = Path(tempfile.mkdtemp(prefix=f"verify-{bug_id}-"))
            (scratch_out / "analysis.md").write_text(result.analysis)
            (scratch_out / "summary.md").write_text(result.summary)
            judgment, judge_cost = await run_verify(
                worktree_path=worktree_path,
                scratch_out=scratch_out,
                bug_id=bug_id,
                failure_commit=failure_commit,
                ground_truth=GroundTruth(gh_fix_commits=gh_fix_commits),
                agent_diff=diff,
                model=self.judge_model,
            )
            output["verify"] = {
                "judgment": judgment.model_dump(),
                "cost_usd": judge_cost,
            }
            return output
        finally:
            self.worktree_mgr.cleanup(wt_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build repair evaluation")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--parallelism", type=int, default=8)
    parser.add_argument("--firefox-repo", default=os.environ.get("FIREFOX_GIT_REPO"))
    parser.add_argument("--dataset", default="build_repair_one_commit_eval")
    parser.add_argument("--judge-model", default=VERIFY_MODEL)
    parser.add_argument("--no-try-push", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    if not args.firefox_repo:
        parser.error("--firefox-repo or FIREFOX_GIT_REPO env var is required")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    os.environ["WEAVE_PARALLELISM"] = str(args.parallelism)
    weave.init("bugbug-build-repair-eval")

    dataset = weave.ref(args.dataset).get()
    if args.limit:
        dataset.rows = dataset.rows[: args.limit]
    logger.info("Loaded dataset %s (%s rows)", args.dataset, len(dataset.rows))

    scorers = [
        BasicMetricsScorer(num_trials=args.trials),
        BuildPassRateScorer(num_trials=args.trials),
        LLMFixMatchingScorer(num_trials=args.trials),
    ]
    model = BuildRepairModel(
        firefox_repo=args.firefox_repo,
        no_try_push=args.no_try_push,
        judge_model=args.judge_model,
    )
    evaluation = weave.Evaluation(
        name="build-repair",
        dataset=dataset,
        scorers=scorers,
        trials=args.trials,
    )
    results = asyncio.run(evaluation.evaluate(model))
    logger.info("Evaluation results: %s", results)


if __name__ == "__main__":
    main()
