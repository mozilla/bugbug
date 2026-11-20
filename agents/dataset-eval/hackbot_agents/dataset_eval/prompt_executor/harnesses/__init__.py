# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Coding harnesses the prompt executor can run, behind one interface.

Each runs a developer's tool unmodified in the tree. Their conversations are
expressed as claude-agent-sdk messages, so one recorder builds the transcript
and the runtime's ``Reporter`` logs it, the same for every harness.
"""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from weave.conversation import Usage

# Added to every harness's system prompt: the run is headless, so the session
# ends as soon as the agent stops.
UNATTENDED = """\
You are running unattended: nobody is watching or can answer, and the session \
ends as soon as you stop. Do not ask questions or wait for replies. Finish the \
task in this session: wait for any build or test run you start to complete, \
rather than leaving it in the background, before you stop, and state the outcome \
in your final message. Complete the work that you asked to do in full, not a part of it with a follow-up."""

# Credential variables per vendor; each harness sees only its own vendor's.
VENDOR_PREFIXES = {
    "anthropic": ("ANTHROPIC_",),
    "openai": ("CODEX_", "OPENAI_"),
}


@dataclass
class Run:
    """What a harness did.

    ``transcript`` holds report turns (``user``, ``assistant``, ``tool_call``,
    ``tool_result``); ``usage`` maps each model that served the run to its
    ``input_tokens``, ``output_tokens`` and cache token counts.
    """

    message: str = ""
    transcript: list[dict] = field(default_factory=list)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    models: list[str] = field(default_factory=list)
    error: str | None = None
    tool_errors: int = 0
    # As the harness itself reports it, when it does.
    cost_usd: float | None = None


def record(run: Run, msg) -> None:
    """Add a message of the main conversation to the run's transcript."""
    if isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
        if msg.model not in run.models:
            run.models.append(msg.model)
        thinking = ""
        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                thinking += block.thinking
            elif isinstance(block, TextBlock):
                run.transcript.append(
                    {"role": "assistant", "content": block.text, "thinking": thinking}
                )
                thinking = ""
            elif isinstance(block, ToolUseBlock):
                run.transcript.append(
                    {
                        "role": "tool_call",
                        "name": block.name,
                        "content": json.dumps(block.input, indent=2, default=str),
                        "thinking": thinking,
                    }
                )
                thinking = ""
        if thinking:
            run.transcript.append(
                {"role": "assistant", "content": "", "thinking": thinking}
            )
    elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                run.tool_errors += bool(block.is_error)
                content = block.content
                if isinstance(content, list):
                    content = "\n".join(c.get("text", "") for c in content)
                run.transcript.append({"role": "tool_result", "content": content or ""})


def trace(
    turn,
    messages: list,
    provider: str,
    usage: dict[str, int] | None = None,
    model: str = "",
) -> None:
    """Record messages on a Weave conversation turn, for harnesses Weave does not trace.

    Each tool call gets a span. The text and thinking are one model response's,
    recorded on one chat span with its ``usage``, when the harness reports it.
    ``model`` names the response when no message does.
    """
    results = {
        block.tool_use_id: block
        for msg in messages
        if isinstance(msg, UserMessage)
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    }
    texts, thoughts = [], []
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        model = msg.model or model
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                with turn.start_tool(
                    name=block.name,
                    arguments=json.dumps(block.input, default=str),
                    tool_call_id=block.id,
                ) as tool:
                    if result := results.get(block.id):
                        tool.result = result.content or ""
                        if result.is_error:
                            tool.record_error(RuntimeError(str(result.content)[-500:]))
            elif isinstance(block, ThinkingBlock):
                thoughts.append(block.thinking)
            elif isinstance(block, TextBlock):
                texts.append(block.text)
    if texts or thoughts or usage:
        with turn.start_llm(model=model, provider_name=provider) as llm:
            if thoughts:
                llm.think("\n".join(thoughts))
            if texts:
                llm.output("\n".join(texts))
            if usage:
                llm.record(usage=_weave_usage(usage))


def _weave_usage(usage: dict[str, int]) -> Usage:
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    return Usage(
        input_tokens=usage.get("input_tokens", 0) + cache_read + cache_write,
        output_tokens=usage.get("output_tokens", 0),
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


def record_usage(turn, usage: dict[str, int], model: str, provider: str) -> None:
    """Record token usage on a Weave conversation turn as one chat span."""
    with turn.start_llm(model=model, provider_name=provider) as llm:
        llm.record(usage=_weave_usage(usage))


class Harness(Protocol):
    name: str
    vendor: str

    async def run(
        self, prompt: str, tree: Path, model: str, effort: str | None, run: Run
    ) -> None:
        """Fill ``run`` as the harness goes, so a cancelled run keeps what it did."""


def add_usage(run: Run, model: str, usage: dict[str, int]) -> None:
    totals = run.usage.setdefault(model, {})
    for key, count in usage.items():
        totals[key] = totals.get(key, 0) + count


class JsonCli:
    """A CLI that takes the prompt on stdin and prints JSON events, one per line."""

    def __init__(self, args: list[str], cwd: Path, env: dict[str, str], stdin: str):
        self.args, self.cwd, self.env, self.stdin = args, cwd, env, stdin
        self.returncode: int | None = None

    async def events(self) -> AsyncIterator[dict]:
        """Its events; other lines go to stderr, and stopping early kills it."""
        proc = await asyncio.create_subprocess_exec(
            *self.args,
            cwd=self.cwd,
            # Some CLIs take their project root from PWD, which is the caller's.
            env={**self.env, "PWD": str(self.cwd)},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=1 << 28,
        )
        try:
            proc.stdin.write(self.stdin.encode())
            proc.stdin.close()
            async for line in proc.stdout:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    sys.stderr.write(line.decode(errors="replace"))
            self.returncode = await proc.wait()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


def harness_env(vendor: str) -> dict[str, str]:
    """The process environment without other vendors' credentials."""
    foreign = tuple(
        prefix
        for name, prefixes in VENDOR_PREFIXES.items()
        if name != vendor
        for prefix in prefixes
    )
    return {k: v for k, v in os.environ.items() if not k.startswith(foreign)}


def get_harness(name: str) -> Harness:
    from .claude_code import ClaudeCode
    from .codex import Codex

    harnesses = {h.name: h for h in (ClaudeCode(), Codex())}
    if name not in harnesses:
        raise ValueError(f"unknown harness {name!r}; known: {', '.join(harnesses)}")
    return harnesses[name]
