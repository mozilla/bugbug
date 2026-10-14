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
from typing import TYPE_CHECKING

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

from hackbot_runtime import wandb_wif

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import Span

log = logging.getLogger("hackbot_runtime")

# Weave project traces land in when the deploy doesn't set WEAVE_PROJECT. Accepts
# either "project" or "entity/project".
DEFAULT_WEAVE_PROJECT = "hackbot-test"

# Weave's tagging namespace: the server surfaces this as `attributes.hackbot.run_id`
# on every call, which the Hackbot UI uses to link a run to its traces.
RUN_ID_SPAN_ATTRIBUTE = "wandb.attributes.hackbot.run_id"


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


class _RunIdSpanProcessor(SpanProcessor):
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id

    def on_start(self, span: "Span", parent_context: "Context | None" = None) -> None:
        span.set_attribute(RUN_ID_SPAN_ATTRIBUTE, self._run_id)


def _tag_spans_with_run_id(run_id: str) -> None:
    provider = otel_trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(_RunIdSpanProcessor(run_id))


@contextlib.contextmanager
def trace_agent(entrypoint: Callable, run_id: str) -> Iterator[None]:
    """Trace the agent run, labelled with the agent's name and tagged with the run id.

    A no-op when tracing isn't configured (no W&B credentials).
    """
    if not _init_weave():
        yield
        return

    from weave.conversation import agent_name_override

    _tag_spans_with_run_id(run_id)
    agent = resolve_agent_name(entrypoint)
    log.info("Enabled Weave tracing for agent %s", agent)
    with agent_name_override(agent):
        yield
