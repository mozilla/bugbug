"""The recordable actions served by the shared ``actions`` server.

Framework-neutral: the claude-sdk and Codex adapters both build their
``actions`` server from these tools and instructions.
"""

from agent_tools.registry import ToolDefinition

from hackbot_runtime.actions import (
    action_records,
    agents,
    bugzilla,
    feedback,
    phabricator,
    slack,
    testrail,
    try_server,
)

ACTIONS_INSTRUCTIONS = """\
Each recorded action returns its ID. A later action can use an earlier action's \
result (`url`, or `bug_id` for a new bug) as `{{actions.<action_id>.<field>}}`; \
it is filled in when the actions are applied. For example, if an action returned \
`(ID: e42219b46c164b2c97785769d6bd1b3e)`, its `url` is \
`{{actions.e42219b46c164b2c97785769d6bd1b3e.url}}`.
"""


def action_tools(types: list[str] | None = None) -> list[ToolDefinition]:
    """The recordable actions served by the ``actions`` server."""
    tools = (
        action_records.TOOLS
        + agents.TOOLS
        + bugzilla.TOOLS
        + phabricator.TOOLS
        + testrail.TOOLS
        + slack.TOOLS
        + try_server.TOOLS
        + feedback.TOOLS
    )
    if types is not None:
        wanted = set(types)
        tools = [t for t in tools if t.dotted in wanted]
    return tools
