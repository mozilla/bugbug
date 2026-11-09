# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt executor: run a coding harness, unmodified, on a prompt in the tree.

The agent under evaluation, run like any hackbot agent: inputs from env, the
result in ``summary.json``, traced by the runtime.
"""

import os
import time
from pathlib import Path

from hackbot_runtime import HackbotAgentResult, HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .harnesses import get_harness


class AgentInputs(BaseSettings):
    prompt: str
    harness: str
    model: str
    effort: str | None = None
    source_repo: Path

    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


class PromptExecutorResult(HackbotAgentResult):
    message: str = ""
    transcript: list[dict] = []
    usage: dict[str, dict[str, int]] = {}
    models: list[str] = []
    error: str | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0


async def main(ctx: HackbotContext) -> PromptExecutorResult:
    inputs = AgentInputs()
    # The agent sees the tree and its task, nothing that says it is being measured.
    for name in ("prompt", "harness", "model", "effort"):
        os.environ.pop(name.upper(), None)
    started = time.monotonic()
    run = await get_harness(inputs.harness).run(
        inputs.prompt, inputs.source_repo, inputs.model, inputs.effort
    )
    return PromptExecutorResult(
        num_turns=sum(t["role"] == "assistant" for t in run.transcript),
        message=run.message,
        transcript=run.transcript,
        usage=run.usage,
        models=run.models,
        error=run.error,
        tool_calls=sum(t["role"] == "tool_call" for t in run.transcript),
        tool_errors=run.tool_errors,
        cost_usd=run.cost_usd,
        latency_s=round(time.monotonic() - started, 1),
    )


if __name__ == "__main__":
    run_async(main)
