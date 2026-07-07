"""Weights & Biases Weave tracing for all hackbot agents.

``weave.init()`` autopatches the Claude Agent SDK, so calling it once in the
runtime before an agent's ``main()`` runs captures every query, model response,
and tool call as a trace with no per-agent instrumentation. It authenticates
either from ``WANDB_API_KEY`` (local/dev) or, in deployment, from the short-lived
identity token that :mod:`hackbot_runtime.wandb_wif` writes -- so the agent
container needs no long-lived W&B credential. Tracing is opt-in: it activates
only when one of those is present, and never fails the run if Weave can't start.

The SDK integration otherwise labels every agent ``claude_agent_sdk``;
``agent_name_override`` relabels the spans with the running agent's name so the
dashboard can tell the agents apart.
"""

import contextlib
import inspect
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from hackbot_runtime import wandb_wif

log = logging.getLogger("hackbot_runtime")

# Weave project traces land in when the deploy doesn't set WEAVE_PROJECT. Accepts
# either "project" or "entity/project".
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
    """Initialize Weave when credentials are available; return whether enabled.

    Credentials come from ``WANDB_API_KEY`` (local) or the identity token file set
    by :mod:`wandb_wif` (deployment via federation). No-op when neither is present.
    """
    if not (
        os.environ.get("WANDB_API_KEY") or os.environ.get(wandb_wif.TOKEN_FILE_ENV)
    ):
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

    A no-op when tracing isn't configured (no W&B credentials).
    """
    if not _init_weave():
        yield
        return

    from weave.conversation import agent_name_override

    agent = resolve_agent_name(entrypoint)
    log.info("Enabled Weave tracing for agent %s", agent)
    with agent_name_override(agent):
        yield
