"""Agent-chaining recordable action.

A run records that another agent should continue its work; the apply side starts
that run once this one has succeeded (see ``handlers/agents_handler.py``). The
downstream run receives this run's id as ``PARENT_RUN_ID`` and its artifacts are
downloaded before its ``main()`` runs (see ``HackbotContext.parent_artifacts_dir``).
"""

from typing import Annotated

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder, confirmation

ACTION_TYPE = "agents.start_run"


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
    if not agent.strip():
        raise ToolError("agent must not be blank")
    action = recorder.record(ACTION_TYPE, {"agent": agent.strip()}, reasoning=reasoning)
    return confirmation(action)


TOOLS = tools_in(__name__)
