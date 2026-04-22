from __future__ import annotations

from pathlib import Path

import yaml

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
BUGZILLA_WRITE_TOOLS = [
    "mcp__bugzilla__update_bug",
    "mcp__bugzilla__add_comment",
    "mcp__bugzilla__add_attachment",
    "mcp__bugzilla__create_bug",
]

# Firefox build/test tools.
FIREFOX_TOOLS = [
    "mcp__firefox__evaluate_testcase",
    "mcp__firefox__build_firefox",
]

# Deployment-stable settings that may be supplied via config YAML.
_CONFIG_KEYS = {"base_url", "source_repo", "rules_dir", "model", "max_turns", "effort"}

# Valid values for the SDK's `effort` knob (adaptive thinking control).
EFFORT_CHOICES = ("low", "medium", "high", "max")


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
