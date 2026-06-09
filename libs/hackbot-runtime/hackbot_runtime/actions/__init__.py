"""Recordable actions for hackbot agents.

``ActionsRecorder`` is the framework-neutral sink whose collected actions the
runtime serialises into ``summary.json``. The action *declarations* live in
domain modules (``bugzilla``, ...) and use the shared ``@tool`` decorator from
agent-tools, so one mechanism backs both read tools and write-actions. The
claude-sdk adapter is ``hackbot_runtime.actions.claude_sdk.actions_server_for``.
"""

from hackbot_runtime.actions import bugzilla
from hackbot_runtime.actions.recorder import ActionsRecorder

__all__ = ["ActionsRecorder", "bugzilla"]
