# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Claude Code, as a developer runs it: the stock preset in the tree."""

import os
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
from hackbot_runtime.claude import Reporter

from . import UNATTENDED, Run, harness_env, record

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
    counts = {
        k: v for k, v in (result.usage or {}).items() if k in _USAGE_KEYS.values()
    }
    return {models[0]: counts} if models else {}


class ClaudeCode:
    name = "claude_code"
    vendor = "anthropic"

    async def run(self, prompt: str, tree: Path, model: str, effort: str | None) -> Run:
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
        run = Run(transcript=[{"role": "user", "content": prompt}])
        with Reporter(verbose=True, log_path=None) as reporter:
            reporter.header(f"{self.name} {model}")
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for msg in client.receive_response():
                    reporter.message(msg)
                    record(run, msg)
                    if isinstance(msg, ResultMessage):
                        run.message = msg.result or ""
                        run.usage = _usage(msg, run.models)
                        run.cost_usd = msg.total_cost_usd
                        if msg.is_error:
                            run.error = msg.result or msg.subtype
        return run
