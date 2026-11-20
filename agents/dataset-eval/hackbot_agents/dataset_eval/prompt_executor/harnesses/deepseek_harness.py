# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""DeepSeek Harness (``dsh``), in its headless profile.

Its models come through otari, as an OpenAI-compatible route. The route, model,
effort and unattended instruction go in a profile patch, which also turns off
uploading the session log to DeepSeek. Weave has no dsh integration, so the
conversation is traced here.
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


def patch(model: otari.Model, effort: str | None, tag: str) -> list[dict]:
    """Profile rows for the run; a row's config replaces the shipped one whole."""
    entry = {"id": model.id}
    if model.context_window:
        entry["contextWindow"] = model.context_window
    if model.max_output_tokens:
        entry["maxTokens"] = model.max_output_tokens
    # A model declared by hand does not reason unless it lists its levels.
    if model.reasoning:
        levels = {"low", "medium", "high"} | ({effort} if effort else set())
        entry["reasoningEfforts"] = {level: level for level in sorted(levels)}
    return [
        {
            "id": "llm-pi-ai",
            "config": {
                "providers": {
                    otari.PROVIDER: {
                        "apiKeyEnv": otari.API_KEY_ENV,
                        "api": "openai-completions",
                        "baseURL": otari.api_url(),
                        "headers": {"Otari-Tags": tag},
                        "models": [entry],
                    }
                }
            },
        },
        {
            "id": "agent-default-model",
            "config": {
                "provider": otari.PROVIDER,
                "model": model.id,
                **({"reasoningEffort": effort} if effort else {}),
            },
        },
        {
            "id": "system-prompt",
            "config": {
                "personaPrefix": "You are a coding agent powered by the {{model}} model.",
                "personaSuffix": UNATTENDED,
            },
        },
        {"id": "session-log-deepseek", "config": {"enabled": False}},
    ]


def to_messages(event: dict, model: str) -> list:
    """A dsh run event as the claude-agent-sdk messages it corresponds to."""
    kind = event.get("type")
    if kind == "text":
        return [AssistantMessage([TextBlock(event["text"])], model)]
    if kind == "thinking":
        return [AssistantMessage([ThinkingBlock(event["text"], "")], model)]
    if kind == "tool_call":
        args = event.get("input")
        args = args if isinstance(args, dict) else {"input": args}
        return [
            AssistantMessage(
                [ToolUseBlock(event["callId"], event["tool"], args)], model
            )
        ]
    if kind == "tool_result":
        failed = event.get("status") == "error"
        content = event.get("result") or json.dumps(event.get("error") or "")
        return [UserMessage([ToolResultBlock(event["callId"], content, failed)])]
    return []


def usage_of(usage: dict) -> dict[str, int]:
    return {
        "input_tokens": usage.get("inputTokens", 0),
        "output_tokens": usage.get("outputTokens", 0),
        "cache_read_input_tokens": usage.get("cacheReadTokens", 0),
        "cache_creation_input_tokens": usage.get("cacheWriteTokens", 0),
    }


class DeepSeekHarness:
    name = "deepseek_harness"
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
        patch_file = Path(tempfile.gettempdir()) / "dsh-patch.yml"
        # JSON is YAML.
        patch_file.write_text(json.dumps(patch(served, effort, tag)))
        env = harness_env(self.vendor)
        env["DSH_HOME"] = str(Path.home() / ".dsh")
        env["DSH_TELEMETRY_DISABLED"] = "1"
        args = [
            "dsh",
            "--profile",
            "headless",
            "--patch",
            str(patch_file),
            "--json",
            "--permission-mode",
            "danger-full-access",
            "--approval-policy",
            "never",
            "-",
        ]
        run.models = [model]
        # A call is traced with its result, or unanswered if the run stops first;
        # a step's text and thinking with its usage, when the step ends.
        calls: dict[str, list] = {}
        step: list = []
        with Reporter(verbose=True, log_path=None) as reporter:
            reporter.header(f"{self.name} {model}")
            cli = JsonCli(args, tree, env, prompt)
            try:
                async for event in cli.events():
                    kind = event.get("type")
                    messages = to_messages(event, model)
                    for msg in messages:
                        reporter.message(msg)
                        record(run, msg)
                    if kind == "tool_call":
                        calls[event["callId"]] = messages
                    elif kind == "tool_result":
                        call = calls.pop(event["callId"], [])
                        trace(traced, call + messages, otari.PROVIDER)
                    else:
                        step += messages
                    if kind == "text":
                        run.message = event["text"]
                    elif kind == "status" and event.get("phase") == "step_end":
                        usage = usage_of(event["usage"]) if event.get("usage") else None
                        if usage:
                            add_usage(run, model, usage)
                        trace(traced, step, otari.PROVIDER, usage, model)
                        step = []
                    elif kind == "final":
                        run.message = event.get("text") or run.message
                        reason = event.get("reason") or {}
                        if reason.get("kind") != "completed":
                            failure = (reason.get("error") or {}).get("message")
                            run.error = failure or reason.get("kind") or "no turn"
                    elif kind == "error":
                        run.error = event.get("message") or "dsh error"
            finally:
                for call in calls.values():
                    trace(traced, call, otari.PROVIDER)
                trace(traced, step, otari.PROVIDER, model=model)
                await otari.settle(run, tag, served, traced)
        if cli.returncode and not run.error:
            run.error = f"dsh exited with status {cli.returncode}"
