# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Claude Code, as a developer runs it: the stock preset in the tree."""

import os
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
)
from hackbot_runtime.claude import Reporter

from . import UNATTENDED, Run, add_usage, harness_env, record

_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
}


def _usage(result: ResultMessage, models: list[str]) -> dict[str, dict[str, int]]:
    if result.model_usage:
        return {
            model: {_USAGE_KEYS[k]: v for k, v in counts.items() if k in _USAGE_KEYS}
            for model, counts in result.model_usage.items()
        }
    return {models[0]: _counts(result.usage)} if models else {}


def _counts(usage: dict | None) -> dict[str, int]:
    return {k: v for k, v in (usage or {}).items() if k in _USAGE_KEYS.values()}


class ClaudeCode:
    name = "claude_code"
    vendor = "anthropic"

    async def run(
        self, prompt: str, tree: Path, model: str, effort: str | None, run: Run
    ) -> None:
        own = harness_env(self.vendor)
        for key in set(os.environ) - set(own):
            del os.environ[key]
        options = ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": UNATTENDED,
            },
            setting_sources=["project"],
            cwd=str(tree),
            permission_mode="bypassPermissions",
            model=model,
            **({"effort": effort} if effort else {}),
        )
        # Until the result's exact totals: the main thread's last usage per message.
        messages: dict[str, AssistantMessage] = {}
        with Reporter(verbose=True, log_path=None) as reporter:
            reporter.header(f"{self.name} {model}")
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for msg in client.receive_response():
                    reporter.message(msg)
                    record(run, msg)
                    if (
                        isinstance(msg, AssistantMessage)
                        and msg.usage
                        and msg.parent_tool_use_id is None
                    ):
                        messages[msg.message_id] = msg
                        run.usage = {}
                        for m in messages.values():
                            add_usage(run, m.model, _counts(m.usage))
                    if isinstance(msg, ResultMessage):
                        run.message = msg.result or ""
                        run.usage = _usage(msg, run.models)
                        run.cost_usd = msg.total_cost_usd
                        if msg.is_error:
                            run.error = msg.result or msg.subtype
