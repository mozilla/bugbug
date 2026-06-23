# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]


# Recordable action types the agent may take, by dotted id. This agent triages
# and plans only: it records a comment with its findings/plan and, at high
# confidence, may propose field updates (e.g. keyword/severity). It never
# creates bugs or attaches files.
ENABLED_ACTION_TYPES = [
    "bugzilla.add_comment",
    "bugzilla.update_bug",
]
