"""Slack-domain recordable actions.

An agent -- or the deterministic code around it, via :func:`record_message` --
records a message it wants delivered to a Slack channel; the apply side posts it
with ``chat.postMessage`` (see ``handlers/slack_handler.py``). Nothing is sent
during the run, so a notification gets the same properties as every other
action: it is visible in the UI before it lands, it is applied at most once
(an already-applied row is never reposted, so a redelivered completion event
cannot double-post), and its text may reference an earlier action's apply-time
result through ``{{actions.<ref>.<field>}}`` -- e.g. the URL of a bug comment
this run posted.
"""

from __future__ import annotations

from typing import Annotated

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder

ACTION_TYPE = "slack.post_message"
HACKBOT_UI_URL = "https://hackbot.moz.tools"

_CHANNEL_DESCRIPTION = (
    'Where to post: a Slack channel ("#example-channel", or a channel '
    'ID), or an audience key the deployment maps to one ("example", '
    '"<Product> :: <Component>").'
)
_TEXT_DESCRIPTION = (
    "Message body in Slack mrkdwn: *bold*, `code`, and <url|label> for links. "
    "Keep it to the few lines the channel needs, with links out for detail."
)


def _params(channel: str, text: str) -> dict[str, str]:
    channel = channel.strip()
    text = text.strip()
    if not channel:
        raise ToolError("channel must not be blank")
    if not text:
        raise ToolError("text must not be blank")
    return {"channel": channel, "text": text}


@tool
async def post_message(
    recorder: ActionsRecorder,
    channel: Annotated[str, Field(description=_CHANNEL_DESCRIPTION)],
    text: Annotated[str, Field(description=_TEXT_DESCRIPTION)],
    reasoning: Annotated[
        str,
        Field(description="Why this channel needs to hear about it (for audit log)."),
    ],
) -> str:
    """Record an intended Slack message.

    Recorded into the run summary for human review -- does not post to Slack.
    """
    recorder.record(ACTION_TYPE, _params(channel, text), reasoning=reasoning)
    return f"Recorded {ACTION_TYPE} (#{len(recorder.actions) - 1})."


def record_message(
    recorder: ActionsRecorder,
    channel: str,
    text: str,
    *,
    ref: str | None = None,
) -> dict:
    """Record a notification the agent was never asked to decide on.

    For a run whose outcome is always worth reporting: the wording is code, not
    a model turn, and the message is recorded once the result exists.
    """
    return recorder.record(ACTION_TYPE, _params(channel, text), ref=ref)


TOOLS = tools_in(__name__)
