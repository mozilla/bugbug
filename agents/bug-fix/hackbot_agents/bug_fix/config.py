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

# Phabricator MCP tool names as exposed to the agent (mcp__<server>__<tool>).
PHABRICATOR_READ_TOOLS = [
    "mcp__phabricator__get_revision",
    "mcp__phabricator__get_revision_comments",
    "mcp__phabricator__get_revision_diff",
]

# Action types that the agent may record during triage/fix runs.
TRIAGE_AND_FIX_ACTIONS = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
    "phabricator.submit_patch",
]

# Action types that the agent may record during follow-up runs on a revision.
PHABRICATOR_FOLLOW_UP_ACTIONS = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
    "phabricator.update_patch",
    "phabricator.add_comment",
]

# Action types available after a Bugzilla needinfo request. In particular this
# mode can create a revision, but cannot update an existing one.
BUGZILLA_NEEDINFO_ACTIONS = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "phabricator.submit_patch",
]

# Firefox build/test tools.
FIREFOX_TOOLS = [
    "mcp__firefox__evaluate_testcase",
    "mcp__firefox__build_firefox",
    "mcp__firefox__evaluate_js_shell",
    "mcp__firefox__bootstrap_firefox",
]
