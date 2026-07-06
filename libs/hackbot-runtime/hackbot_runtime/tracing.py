"""Weights & Biases Weave tracing for all hackbot agents.

``weave.init()`` autopatches the Claude Agent SDK, so calling it once in the
runtime before an agent's ``main()`` runs captures every query, model response,
and tool call as a trace with no per-agent instrumentation. Tracing is opt-in:
it activates only when ``WANDB_API_KEY`` is set (the orchestrator injects it from
Secret Manager) and never fails the run if Weave can't start.

The SDK integration emits OpenTelemetry ``invoke_agent`` spans and, since the
Claude Agent SDK has no agent-name concept, labels them ``claude_agent_sdk`` for
every agent. ``weave.conversation.agent_name_override`` relabels those spans (it
is resolved per span at creation, so it holds across the async SDK calls), which
is what lets the dashboard tell the hackbot agents apart. Weave resolves it from
its OTel span path, so init-level ``attributes`` do not reach these spans.
"""

import contextlib
import inspect
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path

log = logging.getLogger("hackbot_runtime")

# Weave project traces land in when the orchestrator doesn't set WEAVE_PROJECT.
DEFAULT_WEAVE_PROJECT = "hackbot-test"


def resolve_agent_name(entrypoint: Callable) -> str:
    """The running agent's name, derived from its ``main()`` source file.

    Every agent's ``main()`` lives in ``hackbot_agents/<agent>/__main__.py``, so
    the directory holding it is the agent's package. We read the source file path
    (not ``__module__``, which is just ``"__main__"`` under ``python -m``) and
    take that directory name, normalized to the canonical hyphenated form
    (``build_repair`` -> ``build-repair``).
    """
    source = inspect.getfile(entrypoint)
    return Path(source).parent.name.replace("_", "-")


def _init_weave() -> bool:
    """Initialize Weave when configured; return whether tracing is enabled."""
    if not os.environ.get("WANDB_API_KEY"):
        return False

    project = os.environ.get("WEAVE_PROJECT", DEFAULT_WEAVE_PROJECT)
    try:
        import weave

        weave.init(project)
        return True
    except Exception:
        log.exception("Failed to initialize Weave tracing; continuing without it")
        return False


@contextlib.contextmanager
def trace_agent(entrypoint: Callable) -> Iterator[None]:
    """Trace the agent run and label its Weave spans with the agent's name.

    A no-op when tracing isn't configured (no ``WANDB_API_KEY``).
    """
    if not _init_weave():
        yield
        return

    from weave.conversation import agent_name_override

    agent = resolve_agent_name(entrypoint)
    log.info("Enabled Weave tracing for agent %s", agent)
    with agent_name_override(agent):
        yield
