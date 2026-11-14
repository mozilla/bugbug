"""Route an agent's model traffic through otari.ai.

otari.ai (https://otari.ai) is an Anthropic-compatible LLM gateway that records
cost, enforces budgets and can serve a run from another provider. With
``OTARI_ENABLED=1``, the Claude SDK/CLI is pointed at it and every request is
tagged with the run and the agent; otherwise this module does nothing.

Authentication is unchanged: otari.ai answers the same Workload Identity
Federation exchange Anthropic does, so the deploy only points the Anthropic
federation ids at the otari.ai rule and organization. See docs/hackbot/runtime.md.
"""

import logging
import os

log = logging.getLogger("hackbot_runtime.otari")

ENABLED_ENV = "OTARI_ENABLED"

# The SDK appends /v1/messages, and the federation exchange /v1/oauth/token.
BASE_URL = "https://api.otari.ai/api"

# Claude Code reads extra request headers from this variable, one
# ``Name: value`` per line.
CUSTOM_HEADERS_ENV = "ANTHROPIC_CUSTOM_HEADERS"


def configure(run_id: str, agent: str) -> None:
    """Point the Claude SDK/CLI at otari.ai when the deploy enables it."""
    if os.environ.get(ENABLED_ENV) != "1":
        return

    os.environ["ANTHROPIC_BASE_URL"] = BASE_URL

    headers = [
        os.environ.get(CUSTOM_HEADERS_ENV, "").strip(),
        # One session per run, across every query() and subagent it makes.
        f"Otari-Conversation-Id: hackbot-{run_id}",
        f"Otari-Tags: app=hackbot,agent={agent}",
        # otari.ai answers WebSearch for a model with no search of its own.
        "Otari-Web-Search: auto",
    ]
    os.environ[CUSTOM_HEADERS_ENV] = "\n".join(h for h in headers if h)

    log.info("Model traffic routed through otari.ai")
