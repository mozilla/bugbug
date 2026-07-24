# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]


# Searchfox code-search tools (in-process MCP server "searchfox"). Used here to
# map a regressor changeset to the bug that landed it (and vice versa) and to
# read blame when narrowing a range.
SEARCHFOX_TOOLS = [
    "mcp__searchfox__search_identifier",
    "mcp__searchfox__search_text",
    "mcp__searchfox__find_definition",
    "mcp__searchfox__get_function_at_line",
    "mcp__searchfox__get_blame",
    "mcp__searchfox__get_file",
]

# Mozilla VCS / HGMO tools (in-process MCP server "mozilla_vcs"). Read a
# changeset's metadata/diff and file history over HTTP -- used to interpret the
# range mozregression returns and to find the regressor bug for a changeset.
MOZILLA_VCS_TOOLS = [
    "mcp__mozilla_vcs__get_commit_info",
    "mcp__mozilla_vcs__get_commit_diff",
    "mcp__mozilla_vcs__file_history",
]

# mozregression bisection tool (in-process MCP server "mozregression").
MOZREGRESSION_TOOLS = [
    "mcp__mozregression__run_mozregression",
]


# Recordable action types the agent may take, by dotted id. This agent reports a
# regression range: it records a comment with the range and, at high confidence,
# proposes field updates (regressed_by / cf_has_regression_range / clearing the
# regressionwindow-wanted keyword). It never creates bugs or attaches files.
ENABLED_ACTION_TYPES = [
    "bugzilla.add_comment",
    "bugzilla.update_bug",
]
