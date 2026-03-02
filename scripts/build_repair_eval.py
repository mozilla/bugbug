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
import json
import logging
import os
from datetime import datetime
from functools import cached_property
from typing import Any

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

# todo: verify tracing code


def _attr(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_chat_message(data: dict) -> dict | None:
    """Convert a serialized claude_agent_sdk message to OpenAI chat format.

    Content blocks may be dicts (from model_dump) or dataclass instances
    (from vars), so we use _attr() for uniform access.
    """
    msg_type = data.get("type", "")

    if msg_type == "AssistantMessage":
        blocks = data.get("content", [])
        text_parts = []
        tool_calls = []
        for block in blocks:
            text = _attr(block, "text")
            if text is not None:
                text_parts.append(text)
                continue
            name = _attr(block, "name")
            block_id = _attr(block, "id")
            if name is not None and block_id is not None:
                tool_calls.append(
                    {
                        "id": block_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(_attr(block, "input", {})),
                        },
                    }
                )
        if not text_parts and not tool_calls:
            return None
        msg: dict = {"role": "assistant"}
        if text_parts:
            msg["content"] = "\n".join(text_parts)
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    if msg_type == "UserMessage":
        content = data.get("content", "")
        if isinstance(content, list):
            for block in content:
                tool_use_id = _attr(block, "tool_use_id")
                if tool_use_id:
                    block_content = _attr(block, "content", "")
                    return {
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "content": str(block_content) if block_content else "",
                    }

    return None


@weave.op(kind="llm")
def trace_llm_stage(
    stage: str,
    messages: list[dict],
    model: str,
    result_data: dict | None = None,
) -> dict:
    last_assistant = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_assistant = msg["content"]
            break

    result: dict[str, Any] = {
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": last_assistant},
            }
        ],
    }
    if result_data:
        raw_usage = result_data.get("usage", {}) or {}
        input_tokens = raw_usage.get("input_tokens", 0)
        output_tokens = raw_usage.get("output_tokens", 0)
        result["usage"] = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_read_input_tokens": raw_usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": raw_usage.get(
                "cache_creation_input_tokens", 0
            ),
            "total_cost_usd": result_data.get("total_cost_usd", 0),
            "num_turns": result_data.get("num_turns", 0),
        }
    return result


# Per-token costs in USD (standard, non-cached rates).
# Weave uses these for its built-in cost UI; the SDK's total_cost_usd
# (which accounts for cache pricing) is tracked separately as the authoritative cost.
ANTHROPIC_TOKEN_COSTS: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0e-6, 75.0e-6),
    "claude-sonnet-4-6": (3.0e-6, 15.0e-6),
    "claude-haiku-4-5-20251001": (0.8e-6, 4.0e-6),
    "claude-sonnet-4-5-20250929": (3.0e-6, 15.0e-6),
    "claude-opus-4-5-20251101": (15.0e-6, 75.0e-6),
    "claude-opus-4-1-20250805": (15.0e-6, 75.0e-6),
    "claude-sonnet-4-20250514": (3.0e-6, 15.0e-6),
    "claude-3-7-sonnet-20250219": (3.0e-6, 15.0e-6),
    "claude-opus-4-20250514": (15.0e-6, 75.0e-6),
}


def _register_model_costs(client) -> None:
    for model_id, (prompt_cost, completion_cost) in ANTHROPIC_TOKEN_COSTS.items():
        try:
            client.add_cost(
                llm_id=model_id,
                prompt_token_cost=prompt_cost,
                completion_token_cost=completion_cost,
            )
        except Exception as e:
            logger.debug(f"Could not register cost for {model_id}: {e}")


def _make_weave_callback():
    stages: dict[str, dict] = {}

    def on_message(stage: str, data: dict) -> None:
        msg_type = data["type"]
        if msg_type == "stage_start":
            messages = []
            if "system_prompt" in data:
                messages.append({"role": "system", "content": data["system_prompt"]})
            messages.append({"role": "user", "content": data["prompt"]})

            stages[stage] = {
                "model": data["model"],
                "messages": messages,
            }
        elif msg_type == "stage_end":
            if stage in stages:
                s = stages.pop(stage)
                trace_llm_stage(
                    stage=stage,
                    messages=s["messages"],
                    model=s["model"],
                    result_data=data.get("result_data") or None,
                )
        else:
            if stage in stages:
                chat_msg = _to_chat_message(data)
                if chat_msg:
                    stages[stage]["messages"].append(chat_msg)

    return on_message


class BuildRepairModel(weave.Model):
    """Weave Model wrapper that creates a worktree per example and runs BuildRepairTool."""

    firefox_repo: str
    analysis_only: bool = False
    no_try_push: bool = False
    trial_id: int = 0

    @cached_property
    def tool(self) -> BuildRepairTool:
        return BuildRepairTool.create(analysis_only=self.analysis_only, eval_mode=True)

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
            f"Invoking bug {bug_id} (trial={self.trial_id}, "
            f"commit={gh_failure_commits[0][:12]}, {len(failures)} failures)"
        )

        try:
            cutoff = max(
                MODEL_CUTOFF_DATES[self.tool.analysis_model],
                MODEL_CUTOFF_DATES[self.tool.fix_model],
            )
            if datetime.fromisoformat(fix_commit_date).date() < cutoff:
                logger.warning(
                    f"Skipping bug {bug_id}: fix date {fix_commit_date} "
                    f"is before model cutoff {cutoff}"
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
                on_message=_make_weave_callback(),
            )
            logger.info(
                f"Bug {bug_id} completed: error={result.error}, "
                f"diff_len={len(result.diff)}, cost=${result.cost_usd:.4f}, "
                f"turns={result.num_turns}, "
                f"local_build={result.local_build_passed}, "
                f"try_build={result.try_build_passed}"
            )
            return result.model_dump()
        except Exception as e:
            logger.error(f"Bug {bug_id} failed with exception: {e}", exc_info=True)
            return {
                "error": str(e),
                "diff": "",
                "summary": "",
                "analysis": "",
                "cost_usd": 0,
                "num_turns": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "local_build_passed": None,
                "try_build_passed": None,
                "lando_job_id": None,
                "treeherder_url": None,
                "stage1_transcript": [],
                "stage2_transcript": [],
            }
        finally:
            logger.info(f"Bug {bug_id}: cleaning up worktree {wt_name}")
            self.worktree_mgr.cleanup(wt_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build repair evaluation")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--parallelism", type=int, default=8)
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
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logger.info(
        f"Starting evaluation: dataset={args.dataset}, limit={args.limit}, "
        f"trials={args.trials}, parallelism={args.parallelism}, "
        f"analysis_only={args.analysis_only}, no_try_push={args.no_try_push}, "
        f"firefox_repo={args.firefox_repo}"
    )

    os.environ["WEAVE_PARALLELISM"] = str(args.parallelism)
    client = weave.init("bugbug-build-repair-eval")
    _register_model_costs(client)

    dataset = weave.ref(args.dataset).get()
    logger.info(f"Loaded dataset {args.dataset} with {len(dataset.rows)} rows")
    if args.limit:
        dataset.rows = dataset.rows[: args.limit]
        logger.info(f"Limited to {len(dataset.rows)} rows")

    scorers = [BasicMetricsScorer(), LLMFixMatchingScorer()]
    if not args.analysis_only:
        scorers.insert(1, BuildPassRateScorer())
    logger.info(f"Scorers: {[type(s).__name__ for s in scorers]}")

    for trial in range(args.trials):
        logger.info(f"Starting trial {trial + 1}/{args.trials}")
        model = BuildRepairModel(
            firefox_repo=args.firefox_repo,
            analysis_only=args.analysis_only,
            no_try_push=args.no_try_push,
            trial_id=trial,
        )
        evaluation = weave.Evaluation(
            name=f"build-repair-trial-{trial}",
            dataset=dataset,
            scorers=scorers,
        )
        results = asyncio.run(evaluation.evaluate(model))
        logger.info(f"Trial {trial + 1}/{args.trials} results: {results}")

    # TODO: To compute pass@k across trials, collect per-row scores from each
    # trial via the Weave API (weave.ref(...).get() on individual evaluation
    # runs) and pass them to compute_pass_at_k(). The evaluate() return value
    # only contains aggregated summaries, not per-row data.


if __name__ == "__main__":
    main()
