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
dashboard can tell the agents apart. Subagents get the parent agent's name too,
so they don't show up as separate agents, are tagged with their own name, and
get their token usage recorded.
"""

import contextlib
import inspect
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
SUBAGENT_SPAN_ATTRIBUTE = "wandb.attributes.hackbot.subagent"

# ``ResultMessage.usage`` keys -> ``ResultMessage.model_usage`` keys.
_MODEL_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "cache_read_input_tokens": "cacheReadInputTokens",
    "cache_creation_input_tokens": "cacheCreationInputTokens",
    "output_tokens": "outputTokens",
}


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


class _HackbotSpanProcessor(SpanProcessor):
    """Tag every span with the run id, and spans inside a subagent with its type."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id

    def on_start(self, span: "Span", parent_context: "Context | None" = None) -> None:
        span.set_attribute(RUN_ID_SPAN_ATTRIBUTE, self._run_id)
        parent = otel_trace.get_current_span(parent_context)
        subagent = (getattr(parent, "attributes", None) or {}).get(
            SUBAGENT_SPAN_ATTRIBUTE
        )
        if subagent:
            span.set_attribute(SUBAGENT_SPAN_ATTRIBUTE, subagent)


def _tag_spans(run_id: str) -> None:
    provider = otel_trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(_HackbotSpanProcessor(run_id))


@contextlib.contextmanager
def _trace_subagents_as(agent: str) -> Iterator[None]:
    """Fix two gaps in Weave's Claude Agent SDK integration for subagents.

    Naming: Weave names a subagent's ``invoke_agent`` span after its type
    (e.g. ``investigator``), and the Agents view groups by that name, so every
    subagent type shows up as a separate agent. We give the span the parent
    agent's name and keep the type in ``hackbot.subagent``.

    Usage: Weave records only the main thread's ``ResultMessage.usage``, which
    excludes subagents and the CLI's helper model calls. The SDK's per-message
    usage can't fill the gap: it repeats across content blocks and its output
    counts are partial, and a subagent's tool result reports only its last call.
    The only exact source is ``ResultMessage.model_usage``, so the rest of each
    turn's usage is recorded on a chat span per model under the turn, tagged
    with the subagents that ran in it. Weave then prices it like any chat span
    and sums it into the run's cost.

    Weave has no public hooks for either, so this patches ``SubAgent.start``
    and the integration's message handler for the duration of the run.
    """
    try:
        from weave.conversation import SubAgent
        from weave.integrations.claude_agent_sdk import otel_integration
    except ImportError:
        yield
        return

    # Subagent types started since the last ResultMessage, for tagging.
    subagents: set[str] = set()
    # model_usage is cumulative over a session, so keep the previous totals per
    # session to take only this turn's part.
    session_totals: dict[str, dict[str, dict[str, int]]] = {}
    start = SubAgent.start
    process = otel_integration._process_message

    def start_as_agent(self: SubAgent, *, set_current: bool = True) -> SubAgent:
        subagent = self.name
        subagents.add(subagent)
        # Rename before starting: the span name is set at start, and the agent
        # name attribute is rewritten from ``self.name`` when the span ends.
        self.record(name=agent)
        start(self, set_current=set_current)
        self.set_attributes({SUBAGENT_SPAN_ATTRIBUTE: subagent})
        return self

    def record_unattributed_usage(msg: Any, state: Any) -> None:
        totals = {
            model: {
                key: int(usage.get(src, 0) or 0)
                for key, src in _MODEL_USAGE_KEYS.items()
            }
            for model, usage in (msg.model_usage or {}).items()
        }
        previous = session_totals.get(msg.session_id, {})
        session_totals[msg.session_id] = totals
        for model, total in totals.items():
            # Weave already records the main thread's usage, for the main model only.
            main = (msg.usage or {}) if model == state.model else {}
            rest = {
                key: max(
                    0,
                    total[key]
                    - previous.get(model, {}).get(key, 0)
                    - int(main.get(key, 0) or 0),
                )
                for key in _MODEL_USAGE_KEYS
            }
            if not any(rest.values()):
                continue
            # Under the turn, not a subagent span: Weave ends subagent spans on
            # the task notification, before this, and when several subagents
            # run in a turn, model_usage can't split their usage between them.
            with state.turn.start_llm(model=model, provider_name="anthropic") as llm:
                if subagents:
                    llm.set_attributes(
                        {SUBAGENT_SPAN_ATTRIBUTE: ",".join(sorted(subagents))}
                    )
                llm.record(usage=otel_integration._usage_from_result(rest))
        subagents.clear()

    def process_with_subagent_usage(msg: Any, state: Any) -> str | None:
        # Only ResultMessage carries model_usage. Record before Weave processes
        # it, while the turn span is still open.
        if getattr(msg, "model_usage", None) is not None:
            record_unattributed_usage(msg, state)
        return process(msg, state)

    SubAgent.start = start_as_agent
    otel_integration._process_message = process_with_subagent_usage
    try:
        yield
    finally:
        SubAgent.start = start
        otel_integration._process_message = process


@contextlib.contextmanager
def trace_agent(entrypoint: Callable, run_id: str) -> Iterator[None]:
    """Trace the agent run, labelled with the agent's name and tagged with the run id.

    A no-op when tracing isn't configured (no W&B credentials).
    """
    if not _init_weave():
        yield
        return

    from weave.conversation import agent_name_override

    _tag_spans(run_id)
    agent = resolve_agent_name(entrypoint)
    log.info("Enabled Weave tracing for agent %s", agent)
    with agent_name_override(agent), _trace_subagents_as(agent):
        yield
