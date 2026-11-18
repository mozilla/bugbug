# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""One model session proposes one candidate; Python owns all CI decisions."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolError, tool, tools_in
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
)
from hackbot_runtime import AgentError
from hackbot_runtime.claude import Reporter
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)
MAX_FOLLOWUPS = 3
RECOVERY_TIMEOUT_SECONDS = 1800
# Leaves room in the investigation's 8h execution for checkout, lint and push.
SESSION_TIMEOUT_SECONDS = 6 * 60 * 60
# Runtime/orchestrator settings the model's shell must not use: the Lando token
# (local runs push themselves), the run's artifact upload grant, and the
# validation inputs.
HIDDEN_ENV = (
    "LANDO_ACCESS_TOKEN",
    "RESULTS_POLICY_URL",
    "RESULTS_POLICY_FIELDS",
    "PERF_VALIDATION_STATE_URL",
    "PERF_VALIDATION_PATCH_URL",
    "PERF_VALIDATION_ID",
)


class Proposal(BaseModel):
    has_candidate: bool
    summary: str = Field(min_length=1, max_length=2000)
    regression_location: str = Field(
        min_length=1,
        description=(
            "Where the extra work or delay occurs: source paths, functions, "
            "processes/threads and the relevant call path. Distinguish the "
            "culprit's changed code from the bottleneck and the candidate's "
            "edit location. State explicitly if the location is unknown."
        ),
    )
    root_cause: str = Field(
        min_length=1,
        description=(
            "Explain the causal chain from the culprit's behavior change, through "
            "the affected workload and extra work or waiting, to the alerted "
            "metric. Compare behavior before and after. Label hypotheses and "
            "unknowns instead of presenting them as observations."
        ),
    )
    profile_evidence: str = Field(
        min_length=1,
        description=(
            "Supporting before/after observations with profile markers, measured "
            "intervals, timings, artifact/task references and source citations. "
            "Separate directly observed facts from source-based inference, and "
            "include missing evidence and other limitations."
        ),
    )
    correctness_checks: list[str]
    next_steps: str = Field(
        default="",
        description=(
            "What the regression's author could try or investigate next if this "
            "candidate does not bring the numbers back, or if no candidate was "
            "found: alternative fixes, measurements or experiments."
        ),
    )


@dataclass
class CandidateContext:
    proposal: Proposal | None = None


@tool
async def submit_result(ctx: CandidateContext, proposal: Proposal) -> str:
    """Submit this candidate or explain why no solution was found."""
    try:
        ctx.proposal = Proposal.model_validate(proposal)
    except ValidationError as exc:
        raise ToolError(str(exc)) from exc
    return "Candidate recorded; CI validation is performed separately."


async def propose(
    repo: Path,
    evidence: Path,
    previous: list[dict],
    *,
    model: str | None,
    max_turns: int,
    effort: str,
    log: Path,
    usage: dict | None = None,
) -> dict:
    """Run one model session.

    ``usage`` is updated as results arrive, so the caller can report turns and
    cost even when the session fails.
    """
    result = CandidateContext()
    usage = usage if usage is not None else {}

    options = ClaudeAgentOptions(
        cwd=str(repo),
        model=model,
        effort=effort,
        max_turns=max_turns,
        permission_mode="bypassPermissions",
        setting_sources=[],
        # Image tool results can exceed the SDK's default 1 MiB message buffer.
        max_buffer_size=10 * 1024 * 1024,
        system_prompt=(Path(__file__).parent / "prompts/system.md").read_text(),
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            "Edit",
            "Write",
            "mcp__candidate__submit_result",
        ],
        disallowed_tools=["AskUserQuestion", "Task"],
        env=dict.fromkeys(HIDDEN_ENV, ""),
        mcp_servers={
            "candidate": build_sdk_server("candidate", result, tools_in(__name__))
        },
    )
    turns = 0
    cost = 0.0
    prompt = (
        f"Investigate the evidence in {evidence}. Work in {repo}. "
        f"Previous attempts and CI results:\n{json.dumps(previous)}"
    )
    with Reporter(verbose=True, log_path=log) as reporter:
        try:
            async with ClaudeSDKClient(options=options) as client:
                # Keep the same client alive so background tools and conversation
                # context survive a normal response ending without submit_result.
                loop = asyncio.get_running_loop()
                session_deadline = loop.time() + SESSION_TIMEOUT_SECONDS
                async with asyncio.timeout_at(session_deadline) as recovery_timeout:
                    for followup in range(MAX_FOLLOWUPS + 1):
                        message = None
                        await client.query(prompt)
                        async for item in client.receive_response():
                            reporter.message(item)
                            if isinstance(item, ResultMessage):
                                message = item
                                turns += message.num_turns
                                # Results carry the running session cost. Record
                                # usage as it arrives in case the stream fails.
                                if message.total_cost_usd is not None:
                                    cost = message.total_cost_usd
                                usage.update(num_turns=turns, total_cost_usd=cost)
                        if message is None:
                            raise AgentError(
                                "Candidate session ended without an SDK result"
                            )
                        if result.proposal is not None:
                            break
                        if message.is_error:
                            raise AgentError(
                                f"Candidate SDK error: {message.subtype}; "
                                f"turns={turns}; errors={message.errors}"
                            )
                        if followup == MAX_FOLLOWUPS or turns >= max_turns:
                            raise AgentError(
                                "Candidate did not submit a structured result after "
                                f"{followup} follow-ups and {turns} turns"
                            )
                        if followup == 0:
                            recovery_timeout.reschedule(
                                min(
                                    session_deadline,
                                    loop.time() + RECOVERY_TIMEOUT_SECONDS,
                                )
                            )
                        logger.warning(
                            "Candidate has not submitted a result; continuing the same "
                            "session (%s/%s)",
                            followup + 1,
                            MAX_FOLLOWUPS,
                        )
                        prompt = (
                            "You have not called submit_result. Continue this investigation "
                            "in the existing checkout. If lint or another tool is running "
                            "in the background, inspect its output and wait for completion "
                            "using the task tools; do not end with a promise to wait. "
                            "Do not start duplicate lint processes or poll using pgrep "
                            "patterns that match your own waiting command. Finish checks "
                            "and call submit_result, or submit has_candidate=false with "
                            "an accurate explanation if you cannot produce a candidate. "
                            "Do not claim unfinished checks passed. Wrap up now; avoid "
                            "starting a new investigation."
                        )
        except Exception as exc:
            if result.proposal is None:
                if isinstance(exc, TimeoutError):
                    raise AgentError(
                        "Candidate session timed out without submitting a result "
                        f"(recovery limit {RECOVERY_TIMEOUT_SECONDS // 60} minutes, "
                        f"session limit {SESSION_TIMEOUT_SECONDS // 3600} hours)"
                    ) from exc
                raise
            # submit_result is authoritative for the proposal; transport and
            # shutdown failures cannot undo it. The caller still validates the
            # patch and runs lint/CI before accepting a fix.
            logger.warning(
                "SDK failed after submit_result; preserving the proposal and last "
                "reported usage",
                exc_info=True,
            )
    return {
        **result.proposal.model_dump(),
        "num_turns": turns,
        "total_cost_usd": cost,
    }
