# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Coding harnesses the prompt executor can run, behind one interface.

Each runs a developer's tool unmodified in the tree. Their conversations are
expressed as claude-agent-sdk messages, so one recorder builds the transcript
and the runtime's ``Reporter`` logs it, the same for every harness.
"""

import json
import os
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

# Added to every harness's system prompt: the run is headless, so the session
# ends as soon as the agent stops.
UNATTENDED = """\
You are running unattended: nobody is watching or can answer, and the session \
ends as soon as you stop. Do not ask questions or wait for replies. Finish the \
task in this session: wait for any build or test run you start to complete, \
rather than leaving it in the background, before you stop, and state the outcome \
in your final message. Complete the work that you asked to do in full, not a part of it with a follow-up."""

# Credential variables per vendor; each harness sees only its own vendor's.
VENDOR_PREFIXES = {"anthropic": ("ANTHROPIC_",), "openai": ("CODEX_", "OPENAI_")}


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


class Harness(Protocol):
    name: str
    vendor: str

    async def run(
        self, prompt: str, tree: Path, model: str, effort: str | None
    ) -> Run: ...


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
