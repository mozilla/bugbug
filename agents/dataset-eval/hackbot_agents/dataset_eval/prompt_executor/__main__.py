# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt executor: run a coding harness, unmodified, on a prompt in the tree.

The agent under evaluation, run like any hackbot agent: inputs from env, the
result in ``summary.json``, traced by the runtime.
"""

import asyncio
import contextlib
import os
import time
from pathlib import Path

from hackbot_runtime import HackbotAgentResult, HackbotContext, run_async
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from pydantic_settings import BaseSettings, SettingsConfigDict

from .harnesses import Looping, Run, get_harness, otari, otari_wif

# Weave surfaces it as ``attributes.hackbot.harness`` on every call of the run.
HARNESS_SPAN_ATTRIBUTE = "wandb.attributes.hackbot.harness"


class _HarnessTag(SpanProcessor):
    def __init__(self, harness: str) -> None:
        self._harness = harness

    def on_start(self, span, parent_context=None) -> None:
        span.set_attribute(HARNESS_SPAN_ATTRIBUTE, self._harness)


def tag_traces(harness: str) -> None:
    """Tag every span this process traces with the harness, when tracing is on."""
    provider = otel_trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(_HarnessTag(harness))


class AgentInputs(BaseSettings):
    prompt: str
    harness: str
    model: str
    effort: str | None = None
    timeout_s: float | None = None
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
    timed_out: bool = False
    # Stopped for repeating one tool call; see ``LOOP_LIMIT``.
    looped: bool = False


async def main(ctx: HackbotContext) -> PromptExecutorResult:
    inputs = AgentInputs()
    # The agent sees the tree and its task, nothing that says it is being measured.
    for name in ("prompt", "harness", "model", "effort", "timeout_s"):
        os.environ.pop(name.upper(), None)
    tag_traces(inputs.harness)
    started = time.monotonic()
    run = Run(transcript=[{"role": "user", "content": inputs.prompt}])
    timed_out = looped = False
    try:
        harness = get_harness(inputs.harness)
        auth = (
            otari_wif.serving()
            if harness.vendor == otari.PROVIDER
            else contextlib.nullcontext()
        )
        async with auth, asyncio.timeout(inputs.timeout_s):
            await harness.run(
                inputs.prompt, inputs.source_repo, inputs.model, inputs.effort, run
            )
    except TimeoutError:
        timed_out = True
    except Looping:
        looped = True
    return PromptExecutorResult(
        timed_out=timed_out,
        looped=looped,
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
