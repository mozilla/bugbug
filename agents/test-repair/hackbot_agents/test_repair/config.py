# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Models and tool allowlist for the test-repair agent."""

ANALYSIS_MODEL = "claude-opus-5-5"

# The agent a blamed culprit is handed on to for a patch.
FIX_AGENT = "test-repair-fix"

# Where the verdict is reported.
SLACK_CHANNEL = "#sheriff-notifications"

# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]

ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash",
    "Write",
    "WebFetch",
    "WebSearch",
]
