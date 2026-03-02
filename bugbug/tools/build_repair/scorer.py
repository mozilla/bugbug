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
    def score(self, output: dict | None) -> dict:
        if output is None:
            return {
                "successful": False,
                "has_diff": False,
                "cost_usd": 0,
                "num_turns": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        return {
            "successful": output.get("error") is None,
            "has_diff": bool(output.get("diff", "").strip()),
            "cost_usd": output.get("cost_usd", 0),
            "num_turns": output.get("num_turns", 0),
            "input_tokens": output.get("input_tokens", 0),
            "output_tokens": output.get("output_tokens", 0),
            "cache_read_input_tokens": output.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": output.get("cache_creation_input_tokens", 0),
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        n = len(score_rows)
        costs = [r["cost_usd"] for r in score_rows]
        input_toks = [r["input_tokens"] for r in score_rows]
        output_toks = [r["output_tokens"] for r in score_rows]
        summary = {
            "success_rate": sum(r["successful"] for r in score_rows) / n if n else 0,
            "diff_rate": sum(r["has_diff"] for r in score_rows) / n if n else 0,
            "avg_cost_usd": sum(costs) / n if n else 0,
            "total_cost_usd": sum(costs),
            "total_input_tokens": sum(input_toks),
            "total_output_tokens": sum(output_toks),
            "total_cache_read_tokens": sum(
                r["cache_read_input_tokens"] for r in score_rows
            ),
            "total_cache_creation_tokens": sum(
                r["cache_creation_input_tokens"] for r in score_rows
            ),
            "num_examples": n,
        }
        logger.info(f"BasicMetrics summary: {summary}")
        return summary


class BuildPassRateScorer(weave.Scorer):
    """Scores local ./mach build and try push pass rates."""

    @weave.op()
    def score(self, output: dict | None) -> dict:
        if output is None:
            return {
                "local_build_passed": None,
                "try_build_passed": None,
            }
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
        logger.info(f"BuildPassRate summary: {summary}")
        return summary


class LLMFixMatchingScorer(weave.Scorer):
    """Scaffold for LLM-as-a-judge comparing agent fix to ground truth.

    Implementation deferred. Will use a non-Claude LLM to semantically
    compare the agent's diff against the ground truth fix commit.
    """

    @weave.op()
    async def score(self, output: dict | None, gh_fix_commits: list[str]) -> dict:
        if output is None:
            return {
                "match_score": None,
                "match_category": "errored",
            }
        return {
            "match_score": None,
            "match_category": "not_implemented",
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        return {"status": "not_implemented"}


def compute_pass_at_k(
    result_rows: list[dict],
    num_examples: int,
    num_trials: int,
    scorer_name: str,
    metric: str,
) -> dict[str, float]:
    """Compute pass@k from Weave evaluation results with trials.

    Rows are ordered: first num_examples = trial 0, next = trial 1, etc.
    """
    if num_trials < 2:
        return {}

    pass_at: dict[str, float] = {}
    for n in {1, 3, num_trials}:
        if n > num_trials:
            continue
        successes = sum(
            any(
                result_rows[t * num_examples + i]["scores"]
                .get(scorer_name, {})
                .get(metric)
                is True
                for t in range(n)
            )
            for i in range(num_examples)
        )
        pass_at[f"pass@{n}"] = successes / num_examples if num_examples else 0

    all_pass = sum(
        all(
            result_rows[t * num_examples + i]["scores"].get(scorer_name, {}).get(metric)
            is True
            for t in range(num_trials)
        )
        for i in range(num_examples)
    )
    pass_at[f"pass^{num_trials}"] = all_pass / num_examples if num_examples else 0

    return pass_at
