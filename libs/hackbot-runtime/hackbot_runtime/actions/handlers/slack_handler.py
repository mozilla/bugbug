"""Apply-side Slack action: posts a recorded message with ``chat.postMessage``.

Authenticates as a Slack app with a bot token (``SLACK_BOT_TOKEN``, needing the
``chat:write`` scope, plus ``chat:write.public`` for a public channel the app has
not been invited to), through the official ``slack_sdk`` client.

Posts the recorded action as it stands: both the channel and the message are the
recording side's to decide (see ``actions/slack.py``).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from slack_sdk import WebClient

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30


def _client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not configured")
    # The synchronous client, called from an async handler as ``bugzilla_handler``
    # calls ``requests``: one request per applied action doesn't earn an aiohttp
    # dependency.
    return WebClient(token=token, timeout=_TIMEOUT_SECONDS)


class PostMessageHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        channel = params["channel"]
        try:
            # Slack reports application errors in a 200 body; the SDK raises
            # ``SlackApiError`` on them, so "channel_not_found" cannot read as a
            # delivered message.
            response = _client().chat_postMessage(channel=channel, text=params["text"])
        except Exception as exc:
            log.exception("Failed to post to Slack channel %s", channel)
            return ActionResult.failed(str(exc))

        return ActionResult.ok({"channel": response["channel"], "ts": response["ts"]})
