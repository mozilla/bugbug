# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""OpenAI Codex, through its Python SDK, as a developer runs it in the tree.

The container is the sandbox, as it is for Claude Code, so Codex runs with full
access and never asks for approval. Its results do not name the serving model, so
the requested one is reported. Weave has no Codex integration, so the
conversation is traced here, as the Claude Agent SDK integration does for Claude
Code.
"""

import json
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from hackbot_runtime.claude import Reporter
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex._run import _collect_async_turn_result
from weave.conversation import Usage, start_conversation
from weave.conversation.agent_context import resolve_agent_name

from . import UNATTENDED, Run, harness_env, record


def to_messages(item: dict, model: str) -> list:
    """A Codex thread item as the claude-agent-sdk messages it corresponds to."""
    kind, item_id = item.get("type"), item.get("id", "")
    if kind == "userMessage":
        return []
    if kind == "agentMessage":
        return [AssistantMessage([TextBlock(item.get("text", ""))], model)]
    if kind == "reasoning":
        text = "\n".join(item.get("summary") or item.get("content") or [])
        return [AssistantMessage([ThinkingBlock(text, "")], model)]
    if kind == "commandExecution":
        failed = item.get("exit_code") not in (0, None)
        return [
            AssistantMessage(
                [ToolUseBlock(item_id, "shell", {"command": item.get("command", "")})],
                model,
            ),
            UserMessage(
                [ToolResultBlock(item_id, item.get("aggregated_output") or "", failed)]
            ),
        ]
    details = {k: v for k, v in item.items() if k not in ("id", "type")}
    return [
        AssistantMessage([ToolUseBlock(item_id, kind or "unknown", details)], model)
    ]


def normalize(items: list[dict], usage: dict | None, model: str) -> Run:
    """A turn's items and total token usage, in the evaluator's shapes."""
    run = Run(models=[model])
    for item in items:
        for msg in to_messages(item, model):
            record(run, msg)
    total = (usage or {}).get("total") or {}
    cached = total.get("cached_input_tokens", 0)
    run.usage = {
        model: {
            "input_tokens": total.get("input_tokens", 0) - cached,
            "cache_read_input_tokens": cached,
            "output_tokens": total.get("output_tokens", 0),
        }
    }
    return run


def trace(turn, messages: list) -> None:
    """Record one item's messages on a Weave conversation turn."""
    results = {
        block.tool_use_id: block
        for msg in messages
        if isinstance(msg, UserMessage)
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    }
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
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
            else:
                with turn.start_llm(model=msg.model, provider_name="openai") as llm:
                    if isinstance(block, ThinkingBlock):
                        llm.think(block.thinking)
                    else:
                        llm.output(block.text)


async def _reported(stream, reporter: Reporter, turn, model: str):
    """The turn's notifications, reporting and tracing each item as it completes."""
    async for event in stream:
        if event.method == "item/completed":
            item = getattr(event.payload.item, "root", event.payload.item).model_dump()
            messages = to_messages(item, model)
            for msg in messages:
                reporter.message(msg)
            trace(turn, messages)
        yield event


def _record_usage(turn, usage: dict[str, int], model: str) -> None:
    """Codex reports only the turn's total, recorded as one chat span."""
    cached = usage.get("cache_read_input_tokens", 0)
    with turn.start_llm(model=model, provider_name="openai") as llm:
        llm.record(
            usage=Usage(
                input_tokens=usage.get("input_tokens", 0) + cached,
                output_tokens=usage.get("output_tokens", 0),
                cache_read_input_tokens=cached,
            )
        )


class Codex:
    name = "codex"
    vendor = "openai"

    async def run(self, prompt: str, tree: Path, model: str, effort: str | None) -> Run:
        env = harness_env(self.vendor)
        conversation = start_conversation(
            agent_name=resolve_agent_name(self.name), model=model
        )
        with (
            conversation,
            conversation.start_turn(
                user_message=prompt, system_instructions=[UNATTENDED]
            ) as traced,
        ):
            return await self._run(prompt, tree, model, effort, env, traced)

    async def _run(self, prompt, tree, model, effort, env, traced) -> Run:
        with Reporter(verbose=True, log_path=None) as reporter:
            reporter.header(f"{self.name} {model}")
            async with AsyncCodex(CodexConfig(cwd=str(tree), env=env)) as codex:
                if key := env.get("CODEX_API_KEY") or env.get("OPENAI_API_KEY"):
                    await codex.login_api_key(key)
                thread = await codex.thread_start(
                    model=model,
                    cwd=str(tree),
                    sandbox=Sandbox.full_access,
                    approval_mode=ApprovalMode.deny_all,
                    developer_instructions=UNATTENDED,
                    ephemeral=True,
                )
                # The SDK is pinned, so its result collector can be reused on the
                # reported stream instead of consuming the turn twice.
                turn = await thread.turn(prompt, effort=effort)
                result = await _collect_async_turn_result(
                    _reported(turn.stream(), reporter, traced, model),
                    turn_id=turn.id,
                )
        items = [getattr(i, "root", i).model_dump() for i in result.items]
        usage = result.usage.model_dump() if result.usage else None
        run = normalize(items, usage, model)
        _record_usage(traced, run.usage[model], model)
        run.transcript.insert(0, {"role": "user", "content": prompt})
        run.message = result.final_response or ""
        if result.error:
            run.error = getattr(result.error, "message", None) or str(result.error)
        return run
