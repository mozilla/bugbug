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


def _params(
    channel: str, text: str | None, blocks: list[dict] | None = None
) -> dict[str, str | list[dict]]:
    if not channel:
        raise ToolError("channel must not be blank")
    if not text and not blocks:
        raise ToolError("either 'text' or 'blocks' must be provided")

    params: dict[str, str | list[dict]] = {"channel": channel, "text": text}

    if blocks:
        params["blocks"] = blocks

    return params


@tool
async def post_message(
    recorder: ActionsRecorder,
    channel: Annotated[
        str,
        Field(
            description=(
                'Where to post: a Slack channel ("#example-channel") or a channel ID.'
            )
        ),
    ],
    text: Annotated[
        str,
        Field(
            description=(
                "Message body in Slack mrkdwn: *bold*, `code`, and <url|label> for "
                "links. Keep it to the few lines the channel needs, with links out "
                "for detail."
            )
        ),
    ],
    reasoning: Annotated[
        str,
        Field(description="Why this channel needs to hear about it (for audit log)."),
    ],
) -> str:
    """Record an intended Slack message.

    Recorded into the run summary for human review -- does not post to Slack.
    """
    action = recorder.record(ACTION_TYPE, _params(channel, text), reasoning=reasoning)
    return f"Recorded {ACTION_TYPE} (ID: {action['action_id']})."


def record_message(
    recorder: ActionsRecorder,
    channel: str,
    text: str | None = None,
    blocks: list[dict] | None = None,
    *,
    ref: str | None = None,
) -> dict:
    """Record a notification the agent was never asked to decide on.

    For a run whose outcome is always worth reporting: the wording is code, not
    a model turn, and the message is recorded once the result exists.
    """
    return recorder.record(ACTION_TYPE, _params(channel, text, blocks), ref=ref)


TOOLS = tools_in(__name__)
