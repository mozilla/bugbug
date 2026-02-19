# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from logging import getLogger

import weave

logger = getLogger(__name__)


class BasicMetricsScorer(weave.Scorer):
    """Scores success rate, diff production rate, cost, and turn count."""

    @weave.op()
    def score(self, output: dict) -> dict:
        return {
            "successful": output.get("error") is None,
            "has_diff": bool(output.get("diff", "").strip()),
            "cost_usd": output.get("cost_usd", 0),
            "num_turns": output.get("num_turns", 0),
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        n = len(score_rows)
        costs = [r["cost_usd"] for r in score_rows]
        summary = {
            "success_rate": sum(r["successful"] for r in score_rows) / n if n else 0,
            "diff_rate": sum(r["has_diff"] for r in score_rows) / n if n else 0,
            "avg_cost_usd": sum(costs) / n if n else 0,
            "total_cost_usd": sum(costs),
            "num_examples": n,
        }
        logger.info("BasicMetrics summary: %s", summary)
        return summary


class BuildPassRateScorer(weave.Scorer):
    """Scores local ./mach build and try push pass rates."""

    @weave.op()
    def score(self, output: dict) -> dict:
        return {
            "local_build_passed": output.get("local_build_passed"),
            "try_build_passed": output.get("try_build_passed"),
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        n = len(score_rows)
        local_passed = sum(1 for r in score_rows if r["local_build_passed"] is True)
        try_known = [r for r in score_rows if r["try_build_passed"] is not None]
        try_passed = sum(1 for r in try_known if r["try_build_passed"] is True)
        summary = {
            "local_build_pass_rate": local_passed / n if n else 0,
            "local_builds_passed": local_passed,
            "try_build_pass_rate": try_passed / len(try_known) if try_known else 0,
            "try_builds_passed": try_passed,
            "try_builds_timed_out": n - len(try_known),
            "num_examples": n,
        }
        logger.info("BuildPassRate summary: %s", summary)
        return summary


class LLMFixMatchingScorer(weave.Scorer):
    """Scaffold for LLM-as-a-judge comparing agent fix to ground truth.

    Implementation deferred. Will use a non-Claude LLM to semantically
    compare the agent's diff against the ground truth fix commit.
    """

    @weave.op()
    async def score(self, output: dict, gh_fix_commits: list[str]) -> dict:
        return {
            "match_score": None,
            "match_category": "not_implemented",
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        return {"status": "not_implemented"}


def compute_pass_at_k(
    trial_results: list[list[dict]],
    metric: str,
) -> dict:
    """Compute pass@k metrics across multiple trial runs.

    Args:
        trial_results: list of k trial result lists, each with per-example scores
        metric: which boolean metric to use (e.g. "local_build_passed", "successful")

    Returns:
        pass@1, pass@3, pass@k and pass^k metrics
    """
    k = len(trial_results)
    num_examples = len(trial_results[0])

    pass_at = {}
    for n in [1, 3, k]:
        if n > k:
            continue
        successes = sum(
            any(trial_results[t][i][metric] is True for t in range(n))
            for i in range(num_examples)
        )
        pass_at[f"pass@{n}"] = successes / num_examples

    all_pass = sum(
        all(trial_results[t][i][metric] is True for t in range(k))
        for i in range(num_examples)
    )
    pass_at[f"pass^{k}"] = all_pass / num_examples

    return pass_at
