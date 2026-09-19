"""Listeners for clicks on the interactive elements of a message this app posted."""

import re

from slack_bolt.async_app import AsyncApp

from app.slack.listeners.actions.start_agent_run import start_agent_run_callback


def register(app: AsyncApp) -> None:
    app.action(
        {
            "type": "block_actions",
            "action_id": re.compile(r"^start_agent_run:"),
        }
    )(start_agent_run_callback)
