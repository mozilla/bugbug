"""Build the claude-agent-sdk ``actions`` MCP server from recordable actions.

Thin wrapper over agent-tools' generic adapter: the ``ActionsRecorder`` is the
tool context, and tools are namespace-prefixed (one ``actions`` server hosts
every domain). Requires the ``claude-sdk`` optional extra.
"""

from pathlib import Path

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import tool_name_for

from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions import action_records as _action_records
from hackbot_runtime.actions import bugzilla as _bugzilla
from hackbot_runtime.actions import phabricator as _phabricator
from hackbot_runtime.actions import slack as _slack
from hackbot_runtime.actions import testrail as _testrail
from hackbot_runtime.actions import try_server as _try_server
from hackbot_runtime.actions.recorder import ActionsRecorder

INSTRUCTIONS = """\
Each recorded action returns its ID. A later action can use an earlier action's \
result (`url`, or `bug_id` for a new bug) as `{{actions.<action_id>.<field>}}`; \
it is filled in when the actions are applied. For example, if an action returned \
`(ID: e42219b46c164b2c97785769d6bd1b3e)`, its `url` is \
`{{actions.e42219b46c164b2c97785769d6bd1b3e.url}}`.
"""


def actions_server_for(
    recorder: ActionsRecorder | None,
    types: list[str] | None = None,
    *,
    fallback_artifacts_dir: Path = Path("artifacts"),
):
    """Return ``(recorder, sdk_server)`` for the enabled recordable actions.

    ``recorder=None`` creates a local recorder that copies attachments under
    ``fallback_artifacts_dir`` (standalone/script runs with no uploader).
    ``types`` selects a subset by dotted id (e.g. ``bugzilla.update_bug``);
    ``None`` exposes all.
    """
    if recorder is None:
        recorder = ActionsRecorder(artifacts_dir=fallback_artifacts_dir)
    tools = (
        _action_records.TOOLS
        + _bugzilla.TOOLS
        + _phabricator.TOOLS
        + _testrail.TOOLS
        + _slack.TOOLS
        + _try_server.TOOLS
    )
    if types is not None:
        wanted = set(types)
        tools = [t for t in tools if t.dotted in wanted]
    return recorder, build_sdk_server(
        ACTIONS_SERVER_NAME,
        recorder,
        tools,
        prefix_namespace=True,
        instructions=INSTRUCTIONS,
    )


def actions_to_tool_names(types: list[str]) -> list[str]:
    """claude-agent-sdk tool ids for the given action types.

    e.g. ``"bugzilla.update_bug"`` -> ``"mcp__actions__bugzilla_update_bug"``.
    Kept beside ``actions_server_for`` so the ids stay in sync with the server it
    builds (same server name + tool-name mapping).
    """
    return [f"mcp__{ACTIONS_SERVER_NAME}__{tool_name_for(t)}" for t in types]
