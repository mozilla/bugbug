# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""OpenCode, through ``opencode run --format json``, as a developer runs it in the tree.

Its models come through otari, configured as a custom provider. OpenCode has no
flag to append to the system prompt, so the unattended instruction is an
instructions file. Weave has no OpenCode integration, so the conversation is
traced here.
"""

import json
import tempfile
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


def config(model: otari.Model, tag: str, instructions: Path) -> dict:
    """Inline config: otari as a provider serving ``model``, and the instructions."""
    limit = {
        key: value
        for key, value in (
            ("context", model.context_window),
            ("output", model.max_output_tokens),
        )
        if value
    }
    return {
        "provider": {
            otari.PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": otari.api_url(),
                    "apiKey": f"{{env:{otari.API_KEY_ENV}}}",
                    "headers": {"Otari-Tags": tag},
                },
                "models": {
                    model.id: {"name": model.id, "reasoning": model.reasoning}
                    | ({"limit": limit} if limit else {})
                },
            }
        },
        "instructions": [str(instructions)],
        "share": "disabled",
        "autoupdate": False,
    }


def to_messages(event: dict, model: str) -> list:
    """An OpenCode run event as the claude-agent-sdk messages it corresponds to."""
    kind, part = event.get("type"), event.get("part") or {}
    if kind == "text":
        return [AssistantMessage([TextBlock(part.get("text", ""))], model)]
    if kind == "reasoning":
        return [AssistantMessage([ThinkingBlock(part.get("text", ""), "")], model)]
    if kind == "tool_use":
        state = part.get("state") or {}
        failed = state.get("status") == "error"
        output = state.get("error") if failed else state.get("output")
        return [
            AssistantMessage(
                [ToolUseBlock(part["callID"], part["tool"], state.get("input", {}))],
                model,
            ),
            UserMessage([ToolResultBlock(part["callID"], output or "", failed)]),
        ]
    return []


def usage_of(part: dict) -> dict[str, int]:
    """A step's tokens; reasoning is billed as output."""
    tokens = part.get("tokens") or {}
    cache = tokens.get("cache") or {}
    return {
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0) + tokens.get("reasoning", 0),
        "cache_read_input_tokens": cache.get("read", 0),
        "cache_creation_input_tokens": cache.get("write", 0),
    }


class OpenCode:
    name = "opencode"
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
        instructions = Path(tempfile.gettempdir()) / "instructions.md"
        instructions.write_text(UNATTENDED)
        env = harness_env(self.vendor)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config(served, tag, instructions))
        args = [
            "opencode",
            "run",
            "--format",
            "json",
            "--model",
            f"{otari.PROVIDER}/{model}",
            "--auto",
            "--thinking",
            *(["--variant", effort] if effort else []),
        ]
        run.models = [model]
        with Reporter(verbose=True, log_path=None) as reporter:
            reporter.header(f"{self.name} {model}")
            cli = JsonCli(args, tree, env, prompt)
            # A step is one model response; it is traced when its usage arrives.
            step: list = []
            try:
                async for event in cli.events():
                    kind = event.get("type")
                    messages = to_messages(event, model)
                    for msg in messages:
                        reporter.message(msg)
                        record(run, msg)
                    step += messages
                    if kind == "text":
                        run.message = event["part"].get("text", "")
                    elif kind == "step_finish":
                        usage = usage_of(event["part"])
                        add_usage(run, model, usage)
                        trace(traced, step, otari.PROVIDER, usage, model)
                        step = []
                    elif kind == "error":
                        error = event.get("error") or {}
                        run.error = (error.get("data") or {}).get("message") or str(
                            error.get("name", error)
                        )
            finally:
                trace(traced, step, otari.PROVIDER, model=model)
                await otari.settle(run, tag, served, traced)
        if cli.returncode and not run.error:
            run.error = f"opencode exited with status {cli.returncode}"
