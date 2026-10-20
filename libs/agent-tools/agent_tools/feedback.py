"""Developer-feedback tool: lets an agent report problems it runs into.

Framework-neutral: the tool hands each report to the ``report`` callable in its
:class:`FeedbackContext`, so the caller decides where reports go (Hackbot
agents send them to Sentry via ``HackbotContext.feedback``).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from agent_tools.registry import tool, tools_in

Severity = Literal["info", "warning", "error"]


@dataclass
class FeedbackContext:
    """Holds the sink that receives each report as ``(severity, title, message)``."""

    report: Callable[[Severity, str, str], None]


@tool
async def developer_feedback(
    ctx: FeedbackContext,
    severity: Annotated[
        Severity,
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
                "The symptom you observed, what you tried, and what would unblock "
                "you. Never quote bug content."
            )
        ),
    ],
) -> str:
    """Report a problem to the Hackbot developers.

    Report one problem per call. Reporting is never a failure: report the
    problem, then continue your task.
    """
    ctx.report(severity, title, message)
    return "Reported to the Hackbot developers. Continue your task."


TOOLS = tools_in(__name__)
