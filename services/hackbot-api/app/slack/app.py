"""The Bolt app that handles every Slack interaction this service receives."""

from __future__ import annotations

import logging

from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

from app.config import settings
from app.slack.listeners import register_listeners

log = logging.getLogger(__name__)


def build_slack_app() -> AsyncApp:
    """The configured Bolt app, with every listener registered on it."""
    app = AsyncApp(
        signing_secret=settings.slack.signing_secret,
        token=settings.slack.bot_token,
        raise_error_for_unhandled_request=True,
        process_before_response=True,
        logger=log,
    )
    register_listeners(app)
    return app


def build_request_handler() -> AsyncSlackRequestHandler:
    """The adapter that lets a FastAPI route hand a request to Bolt."""
    return AsyncSlackRequestHandler(build_slack_app())
