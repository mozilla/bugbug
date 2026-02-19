# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Standalone CLI for build repair evaluation.

Usage:
    python scripts/build_repair_eval.py
    python scripts/build_repair_eval.py --analysis-only
    python scripts/build_repair_eval.py --trials 3
    python scripts/build_repair_eval.py --limit 5
    python scripts/build_repair_eval.py --parallelism 4
    python scripts/build_repair_eval.py --no-try-push
"""

import argparse
import asyncio
import logging
import os
from datetime import date
from functools import cached_property

import weave

from bugbug.tools.build_repair.agent import AgentResponse, BuildFailure, BuildRepairTool
from bugbug.tools.build_repair.config import MODEL_CUTOFF_DATES
from bugbug.tools.build_repair.scorer import (
    BasicMetricsScorer,
    BuildPassRateScorer,
    LLMFixMatchingScorer,
)
from bugbug.tools.build_repair.worktree import WorktreeManager

logger = logging.getLogger(__name__)


class BuildRepairModel(weave.Model):
    """Weave Model wrapper that creates a worktree per example and runs BuildRepairTool."""

    firefox_repo: str
    analysis_only: bool = False
    no_try_push: bool = False
    trial_id: int = 0

    @cached_property
    def tool(self) -> BuildRepairTool:
        return BuildRepairTool.create(analysis_only=self.analysis_only)

    @cached_property
    def worktree_mgr(self) -> WorktreeManager:
        return WorktreeManager(self.firefox_repo)

    @weave.op()
    async def invoke(
        self,
        bug_id: int,
        pre_fix_bug: dict,
        gh_failure_commits: list[str],
        failures: list[dict],
        fix_commit_date: str,
        **kwargs,
    ) -> dict:
        wt_name = f"bug-{bug_id}-trial-{self.trial_id}"
        logger.info(
            "Invoking bug %d (trial=%d, commit=%s, %d failures)",
            bug_id,
            self.trial_id,
            gh_failure_commits[0][:12],
            len(failures),
        )

        try:
            cutoff = max(
                MODEL_CUTOFF_DATES[self.tool.analysis_model],
                MODEL_CUTOFF_DATES[self.tool.fix_model],
            )
            if date.fromisoformat(fix_commit_date) < cutoff:
                logger.warning(
                    "Skipping bug %d: fix date %s is before model cutoff %s",
                    bug_id,
                    fix_commit_date,
                    cutoff,
                )
                raise ValueError("skipped_data_contamination")

            worktree_path = self.worktree_mgr.create(gh_failure_commits[0], wt_name)

            failure = BuildFailure(
                bug_id=bug_id,
                bug_title=pre_fix_bug["title"],
                bug_comments=pre_fix_bug["comments"],
                git_commit=gh_failure_commits[0],
                failure_tasks=failures,
            )
            result: AgentResponse = await self.tool.run(
                failure,
                worktree_path=worktree_path,
                skip_try_push=self.no_try_push,
            )
            logger.info(
                "Bug %d completed: error=%s, diff_len=%d, cost=$%.4f, turns=%d, "
                "local_build=%s, try_build=%s",
                bug_id,
                result.error,
                len(result.diff),
                result.cost_usd,
                result.num_turns,
                result.local_build_passed,
                result.try_build_passed,
            )
            return result.model_dump()
        except Exception as e:
            logger.error("Bug %d failed with exception: %s", bug_id, e, exc_info=True)
            return {
                "error": str(e),
                "diff": "",
                "summary": "",
                "analysis": "",
                "cost_usd": 0,
                "num_turns": 0,
                "local_build_passed": None,
                "try_build_passed": None,
                "lando_job_id": None,
                "treeherder_url": None,
            }
        finally:
            logger.info("Bug %d: cleaning up worktree %s", bug_id, wt_name)
            self.worktree_mgr.cleanup(wt_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build repair evaluation")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--firefox-repo", default=os.environ.get("FIREFOX_GIT_REPO"))
    parser.add_argument("--dataset", default="build_repair_one_commit_eval")
    parser.add_argument("--analysis-only", action="store_true")
    parser.add_argument("--no-try-push", action="store_true")
    args = parser.parse_args()

    if not args.firefox_repo:
        parser.error("--firefox-repo or FIREFOX_GIT_REPO env var is required")

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info(
        "Starting evaluation: dataset=%s, limit=%s, trials=%d, parallelism=%d, "
        "analysis_only=%s, no_try_push=%s, firefox_repo=%s",
        args.dataset,
        args.limit,
        args.trials,
        args.parallelism,
        args.analysis_only,
        args.no_try_push,
        args.firefox_repo,
    )

    os.environ["WEAVE_PARALLELISM"] = str(args.parallelism)
    weave.init("bugbug-build-repair-eval")

    dataset = weave.ref(args.dataset).get()
    rows = dataset.rows
    logger.info("Loaded dataset %s with %d rows", args.dataset, len(rows))
    if args.limit:
        rows = rows[: args.limit]
        logger.info("Limited to %d rows", len(rows))

    scorers = [BasicMetricsScorer(), LLMFixMatchingScorer()]
    if not args.analysis_only:
        scorers.insert(1, BuildPassRateScorer())
    logger.info("Scorers: %s", [type(s).__name__ for s in scorers])

    for trial in range(args.trials):
        logger.info("Starting trial %d/%d", trial + 1, args.trials)
        model = BuildRepairModel(
            firefox_repo=args.firefox_repo,
            analysis_only=args.analysis_only,
            no_try_push=args.no_try_push,
            trial_id=trial,
        )
        evaluation = weave.Evaluation(
            name=f"build-repair-trial-{trial}",
            dataset=rows,
            scorers=scorers,
        )
        results = asyncio.run(evaluation.evaluate(model))
        logger.info("Trial %d/%d results: %s", trial + 1, args.trials, results)

    # TODO: To compute pass@k across trials, collect per-row scores from each
    # trial via the Weave API (weave.ref(...).get() on individual evaluation
    # runs) and pass them to compute_pass_at_k(). The evaluate() return value
    # only contains aggregated summaries, not per-row data.


if __name__ == "__main__":
    main()
