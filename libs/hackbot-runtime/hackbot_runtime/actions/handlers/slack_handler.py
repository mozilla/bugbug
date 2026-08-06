"""Apply-side Slack action: posts a recorded message with ``chat.postMessage``.

Authenticates as a Slack app with a bot token (``SLACK_BOT_TOKEN``, needing the
``chat:write`` scope, plus ``chat:write.public`` for a public channel the app has
not been invited to), through the official ``slack_sdk`` client.

``SLACK_CHANNELS`` optionally re-routes a recorded audience onto the channel the
deployment posts it to (see :func:`_resolve_channel`). The message itself is
whatever the agent recorded -- composing it is the recording side's job, not this
one's (see ``actions/slack.py``).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from slack_sdk import WebClient

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30
_DEFAULT_CHANNEL_KEY = "default"


def _client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not configured")
    # The synchronous client, called from an async handler as ``bugzilla_handler``
    # calls ``requests``: one request per applied action doesn't earn an aiohttp
    # dependency.
    return WebClient(token=token, timeout=_TIMEOUT_SECONDS)


def _channels() -> dict[str, str]:
    raw = os.environ.get("SLACK_CHANNELS", "")
    if not raw:
        return {}
    try:
        channels = json.loads(raw)
    except ValueError:
        log.error("SLACK_CHANNELS is not valid JSON; ignoring it")
        return {}
    if not isinstance(channels, dict):
        log.error("SLACK_CHANNELS is not a JSON object; ignoring it")
        return {}
    return {str(key): str(value) for key, value in channels.items()}


def _resolve_channel(channel: str) -> str:
    """Map a recorded audience onto the channel the deployment routes it to.

    An audience with no entry of its own falls back to ``default`` when one is
    configured -- a channel belongs to the team that owns the subject, so the map
    is per-audience rather than one address. With nothing configured (or no
    fallback) the recorded value is the channel, so a plain ``#channel`` works
    out of the box.
    """
    channels = _channels()
    return channels.get(channel) or channels.get(_DEFAULT_CHANNEL_KEY) or channel


class PostMessageHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        channel = _resolve_channel(params["channel"])
        try:
            # Slack reports application errors in a 200 body; the SDK raises
            # ``SlackApiError`` on them, so "channel_not_found" cannot read as a
            # delivered message.
            response = _client().chat_postMessage(channel=channel, text=params["text"])
        except Exception as exc:
            log.exception("Failed to post to Slack channel %s", channel)
            return ActionResult.failed(str(exc))

        return ActionResult.ok({"channel": response["channel"], "ts": response["ts"]})
