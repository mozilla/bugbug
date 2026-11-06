"""Agent-chaining recordable action.

A run records that another agent should continue its work; the apply side starts
that run once this one has succeeded (see ``handlers/agents_handler.py``). The
downstream run receives this run's id as ``PARENT_RUN_ID`` and its artifacts are
downloaded before its ``main()`` runs (see ``HackbotContext.parent_artifacts_dir``).

An agent may only start the agents it declared with :func:`allow_agents`; without
that the tool refuses every name.
"""

from collections.abc import Iterable
from typing import Annotated
from weakref import WeakKeyDictionary

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder, confirmation

ACTION_TYPE = "agents.start_run"

_allowed: WeakKeyDictionary[ActionsRecorder, frozenset[str]] = WeakKeyDictionary()


def allow_agents(recorder: ActionsRecorder, agents: Iterable[str]) -> None:
    """Let the run recording into ``recorder`` start only ``agents``."""
    _allowed[recorder] = frozenset(agents)


@tool
async def start_run(
    recorder: ActionsRecorder,
    agent: Annotated[str, Field(description="Name of the agent to start.")],
    reasoning: Annotated[
        str,
        Field(description="Why the work should continue (for audit log)."),
    ],
) -> str:
    """Start another agent on this run's output once this run has finished.

    Recorded into the run summary -- nothing starts until the run succeeds.
    """
    agent = agent.strip()
    allowed = _allowed.get(recorder, frozenset())
    if agent not in allowed:
        raise ToolError(
            f"{agent!r} cannot be started from this run; allowed: "
            + (", ".join(sorted(allowed)) or "none")
        )
    action = recorder.record(ACTION_TYPE, {"agent": agent}, reasoning=reasoning)
    return confirmation(action)


TOOLS = tools_in(__name__)
