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

from hackbot_runtime.actions.recorder import ActionHook, ActionsRecorder

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


def run_link_hook(run_id: str, base_url: str = HACKBOT_UI_URL) -> ActionHook:
    """A hook appending a link back to the run to every message recorded.

    Registered by the agent (``recorder.add_hook(ACTION_TYPE, ...)``) rather than
    added where the message is posted: what a notification says belongs with the
    run that decided to send it, and only the agent knows whether its readers
    want the run at all.
    """
    base = base_url.rstrip("/")

    def hook(action: dict) -> None:
        params = action.get("params")
        if not base or not isinstance(params, dict):
            return
        text = params.get("text")
        if isinstance(text, str):
            link = f"<{base}/runs/{run_id}|hackbot run {run_id}>"
            params["text"] = f"{text.rstrip()}\n{link}"

    return hook


TOOLS = tools_in(__name__)
