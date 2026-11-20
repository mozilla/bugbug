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
from openai_codex.types import ThreadTokenUsageUpdatedNotification
from weave.conversation import start_conversation
from weave.conversation.agent_context import resolve_agent_name

from . import UNATTENDED, Run, harness_env, record, record_usage, trace


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


def usage_of(usage: dict) -> dict[str, int]:
    """A thread's token usage so far, in dataset-eval's shape."""
    total = usage.get("total") or {}
    cached = total.get("cached_input_tokens", 0)
    return {
        "input_tokens": total.get("input_tokens", 0) - cached,
        "cache_read_input_tokens": cached,
        "output_tokens": total.get("output_tokens", 0),
    }


async def _reported(stream, reporter: Reporter, turn, model: str, run: Run):
    """The turn's notifications, recording, reporting and tracing each as it arrives."""
    async for event in stream:
        if event.method == "item/completed":
            item = getattr(event.payload.item, "root", event.payload.item).model_dump()
            messages = to_messages(item, model)
            for msg in messages:
                reporter.message(msg)
                record(run, msg)
            trace(turn, messages, "openai")
        elif isinstance(event.payload, ThreadTokenUsageUpdatedNotification):
            run.usage = {model: usage_of(event.payload.token_usage.model_dump())}
        yield event


class Codex:
    name = "codex"
    vendor = "openai"

    async def run(
        self, prompt: str, tree: Path, model: str, effort: str | None, run: Run
    ) -> None:
        env = harness_env(self.vendor)
        run.models = [model]
        conversation = start_conversation(
            agent_name=resolve_agent_name(self.name), model=model
        )
        with (
            conversation,
            conversation.start_turn(
                user_message=prompt, system_instructions=[UNATTENDED]
            ) as traced,
        ):
            try:
                await self._run(prompt, tree, model, effort, env, traced, run)
            finally:
                if run.usage:
                    record_usage(traced, run.usage[model], model, "openai")

    async def _run(self, prompt, tree, model, effort, env, traced, run) -> None:
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
                    _reported(turn.stream(), reporter, traced, model, run),
                    turn_id=turn.id,
                )
        run.message = result.final_response or ""
        if result.error:
            run.error = getattr(result.error, "message", None) or str(result.error)
