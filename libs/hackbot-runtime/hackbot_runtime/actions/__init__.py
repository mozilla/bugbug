"""Recordable actions for hackbot agents.

``ActionsRecorder`` is the framework-neutral sink whose collected actions the
runtime serialises into ``summary.json``. The action *declarations* live in
domain modules (``bugzilla``, ...) and use the shared ``@tool`` decorator from
agent-tools, so one mechanism backs both read tools and write-actions. The
claude-sdk adapter is ``hackbot_runtime.actions.claude_sdk.actions_server_for``.
"""

from hackbot_runtime.actions import (
    action_records,
    bugzilla,
    phabricator,
    slack,
    testrail,
    try_server,
)
from hackbot_runtime.actions.recorder import ActionHook, ActionsRecorder

ACTIONS_SERVER_NAME = "actions"

__all__ = [
    "ACTIONS_SERVER_NAME",
    "ActionHook",
    "ActionsRecorder",
    "action_records",
    "bugzilla",
    "phabricator",
    "slack",
    "testrail",
    "try_server",
]
