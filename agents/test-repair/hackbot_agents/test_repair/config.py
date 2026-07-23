# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Models and tool allowlist for the test-repair agent."""

ANALYSIS_MODEL = "claude-opus-4-8"
FIX_MODEL = "claude-opus-4-8"

# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]

# In-process Firefox build/test MCP tools (reused from agent-tools). The agent
# uses these to reproduce a test failure and verify a proposed fix.
BUILD_TOOL = "mcp__firefox__build_firefox"
FIREFOX_TOOLS = [
    BUILD_TOOL,
    "mcp__firefox__bootstrap_firefox",
    "mcp__firefox__evaluate_testcase",
    "mcp__firefox__evaluate_js_shell",
]

# Built-in tools the agent may call alongside the MCP servers. The agent runs in
# an isolated container (permission_mode="bypassPermissions"), and reads the
# candidate commit diffs via Bash `git show`.
ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash",
    "Edit",
    "Write",
    "MultiEdit",
    "WebFetch",
    "WebSearch",
]

ADDITIONAL_DIRS = [
    "~/.mozbuild",
    "~/.cache/uv/",
]
