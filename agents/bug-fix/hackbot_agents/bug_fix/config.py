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


# Recordable action types the agent may take, by dotted id.
ENABLED_ACTION_TYPES = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
]

# Firefox build/test tools.
FIREFOX_TOOLS = [
    "mcp__firefox__evaluate_testcase",
    "mcp__firefox__build_firefox",
    "mcp__firefox__evaluate_js_shell",
    "mcp__firefox__bootstrap_firefox",
]
