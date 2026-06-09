from __future__ import annotations

from pathlib import Path

import yaml
from hackbot_runtime.actions.naming import ACTIONS_SERVER_NAME, tool_name_for

# Tools that can modify the source repo — blocked under dry-run.
SOURCE_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]
# Recording action types this agent enables. Served by the in-process
# `actions` MCP server (hackbot_runtime.actions.claude_sdk). Tool calls land
# in summary.json's `actions` array instead of mutating any external system.
# New domains (phabricator, treeherder, ...) just append to this list.
ENABLED_ACTION_TYPES = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
]
# claude-agent-sdk tool identifiers derived from the above, using the shared
# server name and tool-name helper so they stay in sync with the adapter.
ENABLED_ACTION_TOOLS = [
    f"mcp__{ACTIONS_SERVER_NAME}__{tool_name_for(t)}" for t in ENABLED_ACTION_TYPES
]

# Firefox build/test tools.
FIREFOX_TOOLS = [
    "mcp__firefox__evaluate_testcase",
    "mcp__firefox__build_firefox",
    "mcp__firefox__evaluate_js_shell",
    "mcp__firefox__bootstrap_firefox",
]

# Deployment-stable settings that may be supplied via config YAML.
_CONFIG_KEYS = {"base_url", "source_repo", "rules_dir", "model", "max_turns", "effort"}


def load_config(path: Path) -> dict:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    unknown = set(data) - _CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"unknown config key(s) in {path}: {sorted(unknown)}\n"
            f"allowed: {sorted(_CONFIG_KEYS)}"
        )
    return data
