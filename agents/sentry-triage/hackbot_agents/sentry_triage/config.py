# Sentry MCP tool names as exposed to the agent (mcp__<server>__<tool>).
SENTRY_READ_TOOLS = [
    "mcp__sentry__get_issue_event",
]

# Action types that the agent may record during triage/fix runs.
TRIAGE_ACTIONS = [
    "slack.post_findings",
]
