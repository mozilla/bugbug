"""Listeners for clicks on the interactive elements of a message this app posted."""

import re

from slack_bolt.async_app import AsyncApp

from app.slack.listeners.actions.start_agent_run import (
    apply_run_actions_callback,
    start_agent_run_callback,
)


def register(app: AsyncApp) -> None:
    start_agent_run_prefix = re.compile(r"^start_agent_run:")
    apply_run_actions_prefix = re.compile(r"^apply_run_actions:")

    app.action(start_agent_run_prefix)(start_agent_run_callback)
    app.action(apply_run_actions_prefix)(apply_run_actions_callback)
