"""Feedback-domain recordable action: lets an agent report problems it runs into.

The agent records a report for the Hackbot developers; the apply side decides
where it goes (see ``handlers/feedback_handler.py``), so the destination can
change without redeploying the agents.
"""

from typing import Annotated, Literal

from agent_tools.registry import tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder, confirmation

ACTION_TYPE = "feedback.report_problem"


@tool
async def report_problem(
    recorder: ActionsRecorder,
    severity: Annotated[
        Literal["info", "warning", "error"],
        Field(description="How much the problem gets in the way of your task."),
    ],
    title: Annotated[
        str,
        Field(
            description=(
                "Short, general summary of the problem, without run-specific "
                "details like bug IDs or paths, e.g. 'No tool to read bug history'."
            )
        ),
    ],
    message: Annotated[
        str,
        Field(
            description=(
                "The symptom you observed, what you tried, and what would unblock you."
            )
        ),
    ],
) -> str:
    """Report a problem to the Hackbot developers.

    Report one problem per call. Reporting is never a failure: report the
    problem, then continue your task.
    """
    action = recorder.record(
        ACTION_TYPE, {"severity": severity, "title": title, "message": message}
    )
    return confirmation(action)


TOOLS = tools_in(__name__)
