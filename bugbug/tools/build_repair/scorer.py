# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from logging import getLogger

import weave

logger = getLogger(__name__)


def _pass_at_k(
    score_rows: list[dict],
    num_trials: int,
    metric: str,
) -> dict[str, float]:
    """Compute pass@k from scorer rows ordered by trial.

    Rows are ordered: first num_examples = trial 0, next = trial 1, etc.
    Rows may be empty dicts when the model raised an exception.
    """
    num_examples = len(score_rows) // num_trials
    pass_at: dict[str, float] = {}
    for n in sorted({1, 3, num_trials}):
        if n > num_trials:
            continue
        successes = sum(
            any(score_rows[t * num_examples + i].get(metric) is True for t in range(n))
            for i in range(num_examples)
        )
        pass_at[f"pass@{n}"] = successes / num_examples if num_examples else 0

    all_pass = sum(
        all(
            score_rows[t * num_examples + i].get(metric) is True
            for t in range(num_trials)
        )
        for i in range(num_examples)
    )
    pass_at[f"pass^{num_trials}"] = all_pass / num_examples if num_examples else 0

    return pass_at


class BasicMetricsScorer(weave.Scorer):
    """Scores success rate, diff production rate, cost, and turn count."""

    num_trials: int = 1

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
        costs = [r.get("cost_usd", 0) for r in score_rows]
        input_toks = [r.get("input_tokens", 0) for r in score_rows]
        output_toks = [r.get("output_tokens", 0) for r in score_rows]
        summary = {
            "success_rate": sum(r.get("successful", False) for r in score_rows) / n
            if n
            else 0,
            "diff_rate": sum(r.get("has_diff", False) for r in score_rows) / n
            if n
            else 0,
            "avg_cost_usd": sum(costs) / n if n else 0,
            "total_cost_usd": sum(costs),
            "total_input_tokens": sum(input_toks),
            "total_output_tokens": sum(output_toks),
            "total_cache_read_tokens": sum(
                r.get("cache_read_input_tokens", 0) for r in score_rows
            ),
            "total_cache_creation_tokens": sum(
                r.get("cache_creation_input_tokens", 0) for r in score_rows
            ),
            "num_examples": n,
        }
        if self.num_trials > 1:
            summary.update(_pass_at_k(score_rows, self.num_trials, "successful"))
        logger.info(f"BasicMetrics summary: {summary}")
        return summary


class BuildPassRateScorer(weave.Scorer):
    """Scores local ./mach build and try push pass rates."""

    num_trials: int = 1

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
        local_passed = sum(1 for r in score_rows if r.get("local_build_passed") is True)
        try_known = [r for r in score_rows if r.get("try_build_passed") is not None]
        try_passed = sum(1 for r in try_known if r.get("try_build_passed") is True)
        summary = {
            "local_build_pass_rate": local_passed / n if n else 0,
            "local_builds_passed": local_passed,
            "try_build_pass_rate": try_passed / len(try_known) if try_known else 0,
            "try_builds_passed": try_passed,
            "try_builds_timed_out": n - len(try_known),
            "num_examples": n,
        }
        if self.num_trials > 1:
            summary.update(
                _pass_at_k(score_rows, self.num_trials, "local_build_passed")
            )
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
