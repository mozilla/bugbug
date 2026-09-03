# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""LLM-as-a-judge verification of a build-repair fix against ground truth.

Split out of the production agent: this is an evaluation concern. It reads the
agent's artifacts in a worktree and the real landed fix commits, then asks Claude
to score the analysis and the fix.
"""

from __future__ import annotations

from datetime import date
from logging import getLogger
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

logger = getLogger(__name__)

VERIFY_MODEL = "claude-opus-4-8"

# Training-data cutoff per model, for data-contamination filtering. Examples with
# a fix_commit_date before the cutoff may have appeared in training data.
# Source: https://platform.claude.com/docs/en/about-claude/models/overview
MODEL_CUTOFF_DATES = {
    "claude-opus-4-8": date(2026, 1, 1),
    "claude-opus-4-6": date(2025, 8, 1),
    "claude-sonnet-4-6": date(2026, 1, 1),
    "claude-haiku-4-5-20251001": date(2025, 7, 1),
    "claude-sonnet-4-5-20250929": date(2025, 7, 1),
    "claude-opus-4-5-20251101": date(2025, 8, 1),
    "claude-opus-4-1-20250805": date(2025, 3, 1),
    "claude-sonnet-4-20250514": date(2025, 3, 1),
    "claude-3-7-sonnet-20250219": date(2024, 11, 1),
    "claude-opus-4-20250514": date(2025, 3, 1),
}

VERIFY_ALLOWED_TOOLS = [
    "Read",
    "Bash(git show:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(find:*)",
    "Bash(grep:*)",
    "WebFetch(domain:firefox-source-docs.mozilla.org)",
    "WebFetch(domain:searchfox.org)",
]

VERIFY_TEMPLATE = """You are an expert {target_software} code reviewer evaluating an automated build repair agent's work.

Examine the relevant commits using git:
- Failure commit (broke the build): {failure_commit}
- Ground truth fix commit(s) (the real fix that was landed): {ground_truth_commits}

Inspect each commit's changes and read the repair agent's output files:
- {scratch_out}/analysis.md
- {scratch_out}/summary.md
- {scratch_out}/agent_fix.diff (may be empty if no fix was produced)

Evaluate the agent's work on two dimensions:

ANALYSIS:
- Did the agent correctly identify the root cause of the build failure?
- How thorough and accurate is the analysis?

FIX:
- Does the agent's fix address the same files/functions as the ground truth?
- Is the fix semantically equivalent or close to the ground truth?
- Would the fix be acceptable in code review as-is?

Guidelines:
- If agent_fix.diff is empty, set fix_matches_ground_truth=false, fix_quality=0.0, fix_acceptance_probability=0.0
- A fix can be correct even if it differs syntactically from ground truth -- focus on semantic equivalence
- analysis_correct should be true if the agent found the right root cause, even if the explanation is imperfect
- Be calibrated: 0.5 means genuinely uncertain, not a default score

Work autonomously, do not ask questions.
"""


class GroundTruth(BaseModel):
    gh_fix_commits: list[str]


class Judgment(BaseModel):
    analysis_correct: bool
    analysis_quality: float
    analysis_explanation: str
    fix_matches_ground_truth: bool
    fix_quality: float
    fix_explanation: str
    fix_acceptance_probability: float
    fix_acceptance_explanation: str


def is_data_contaminated(fix_commit_date: str, *models: str) -> bool:
    """True when the fix predates the latest training cutoff of the given models.

    Conservative across the models that could have memorized the landed fix: skip
    the example if it predates any of their cutoffs (i.e. the latest one).
    """
    cutoffs = [c for m in models if (c := MODEL_CUTOFF_DATES.get(m)) is not None]
    if not cutoffs:
        return False
    return date.fromisoformat(fix_commit_date[:10]) < max(cutoffs)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=30, jitter=5),
    reraise=True,
)
async def run_verify(
    *,
    worktree_path: Path,
    scratch_out: Path,
    bug_id: int,
    failure_commit: str,
    ground_truth: GroundTruth,
    agent_diff: str,
    target_software: str = "Mozilla Firefox",
    model: str = VERIFY_MODEL,
) -> tuple[Judgment, float]:
    """Judge the agent's analysis and fix. Returns (judgment, cost_usd)."""
    scratch_out.mkdir(parents=True, exist_ok=True)
    (scratch_out / "agent_fix.diff").write_text(agent_diff, encoding="utf-8")

    prompt = VERIFY_TEMPLATE.format(
        target_software=target_software,
        failure_commit=failure_commit,
        ground_truth_commits=" ".join(ground_truth.gh_fix_commits),
        scratch_out=scratch_out,
    )
    options = ClaudeAgentOptions(
        model=model,
        cwd=str(worktree_path),
        allowed_tools=VERIFY_ALLOWED_TOOLS,
        disallowed_tools=["AskUserQuestion", "Task"],
        permission_mode="acceptEdits",
        effort="high",
        output_format={"type": "json_schema", "schema": Judgment.model_json_schema()},
    )

    judgment: Judgment | None = None
    cost = 0.0
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            cost += message.total_cost_usd or 0.0
            structured = getattr(message, "structured_output", None)
            if structured:
                judgment = Judgment.model_validate(structured)
            elif message.result:
                judgment = Judgment.model_validate_json(message.result)

    if judgment is None:
        raise RuntimeError(f"bug {bug_id}: verification produced no structured output")
    return judgment, cost
