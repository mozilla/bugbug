# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""oh-my-pi (``omp``), in headless JSON mode, as a developer runs it in the tree.

Its models come through otari, configured as a custom provider in ``models.yml``.
Weave has no omp integration, so the conversation is traced here.
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
from weave.conversation import start_conversation
from weave.conversation.agent_context import resolve_agent_name

from . import (
    UNATTENDED,
    JsonCli,
    Run,
    add_usage,
    harness_env,
    otari,
    record,
    trace,
)

_USAGE_KEYS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cacheRead": "cache_read_input_tokens",
    "cacheWrite": "cache_creation_input_tokens",
}


def models_config(model: otari.Model, tag: str) -> dict:
    """``models.yml`` with otari as the one provider, serving ``model``."""
    entry = {"id": model.id, "reasoning": model.reasoning}
    if model.context_window:
        entry["contextWindow"] = model.context_window
    if model.max_output_tokens:
        entry["maxTokens"] = model.max_output_tokens
    return {
        "providers": {
            otari.PROVIDER: {
                "baseUrl": otari.api_url(),
                "apiKey": otari.API_KEY_ENV,
                "api": "openai-completions",
                "headers": {"Otari-Tags": tag},
                "models": [entry],
            }
        }
    }


def to_messages(message: dict) -> list:
    """An omp message as the claude-agent-sdk messages it corresponds to."""
    role = message.get("role")
    if role == "assistant":
        blocks = []
        for block in message.get("content", []):
            kind = block.get("type")
            if kind == "text":
                blocks.append(TextBlock(block["text"]))
            elif kind == "thinking":
                blocks.append(ThinkingBlock(block["thinking"], ""))
            elif kind == "toolCall":
                blocks.append(
                    ToolUseBlock(block["id"], block["name"], block.get("arguments", {}))
                )
        return [AssistantMessage(blocks, message.get("model", ""))]
    if role == "toolResult":
        text = "\n".join(
            c["text"] for c in message.get("content", []) if c.get("type") == "text"
        )
        return [
            UserMessage(
                [ToolResultBlock(message["toolCallId"], text, message.get("isError"))]
            )
        ]
    return []


def usage_of(message: dict) -> dict[str, int]:
    usage = message.get("usage") or {}
    return {v: usage.get(k, 0) for k, v in _USAGE_KEYS.items()}


class OhMyPi:
    name = "oh_my_pi"
    vendor = "otari"

    async def run(
        self, prompt: str, tree: Path, model: str, effort: str | None, run: Run
    ) -> None:
        conversation = start_conversation(
            agent_name=resolve_agent_name(self.name), model=model
        )
        with (
            conversation,
            conversation.start_turn(
                user_message=prompt, system_instructions=[UNATTENDED]
            ) as traced,
        ):
            await self._run(prompt, tree, model, effort, traced, run)

    async def _run(self, prompt, tree, model, effort, traced, run) -> None:
        tag, served = otari.new_tag(), await otari.model(model)
        await otari.register_price(served)
        config = Path.home() / ".omp" / "agent" / "models.yml"
        config.parent.mkdir(parents=True, exist_ok=True)
        # JSON is YAML.
        config.write_text(json.dumps(models_config(served, tag)))
        args = [
            "omp",
            "--print",
            "--mode",
            "json",
            "--model",
            f"{otari.PROVIDER}/{model}",
            "--append-system-prompt",
            UNATTENDED,
            "--no-session",
            "--no-title",
            "--wait-async",
            "--auto-approve",
            *(["--thinking", effort] if effort else []),
        ]
        last = {}
        with Reporter(verbose=True, log_path=None) as reporter:
            reporter.header(f"{self.name} {model}")
            cli = JsonCli(args, tree, harness_env(self.vendor), prompt)
            try:
                async for event in cli.events():
                    if event.get("type") == "message_end":
                        message = event["message"]
                        for msg in to_messages(message):
                            reporter.message(msg)
                            record(run, msg)
                        if message.get("role") == "assistant":
                            add_usage(run, message.get("model", ""), usage_of(message))
                            last = message
                            run.message = "\n".join(
                                b["text"]
                                for b in message.get("content", [])
                                if b.get("type") == "text"
                            )
                    elif event.get("type") == "turn_end":
                        message = event["message"]
                        messages = to_messages(message)
                        for result in event.get("toolResults", []):
                            messages += to_messages(result)
                        trace(traced, messages, otari.PROVIDER, usage_of(message))
            finally:
                await otari.settle(run, tag, served, traced)
        if last.get("stopReason") in ("error", "aborted"):
            run.error = last.get("errorMessage") or last["stopReason"]
        elif cli.returncode:
            run.error = f"omp exited with status {cli.returncode}"
