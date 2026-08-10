# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Models and tool allowlist for the test-repair agent."""

ANALYSIS_MODEL = "claude-opus-5"
FIX_MODEL = "claude-opus-5"

# Building costs ten-odd minutes and only verifies a patch the sheriff does not act
# on, so the fix stage proposes an unverified patch unless a run asks otherwise.
SKIP_FIREFOX_BUILD = True

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

# Only the build tool: evaluate_testcase / evaluate_js_shell do not run CI's
# harnesses, so the agent runs the failing test itself with mach over Bash.
BUILD_TOOL = "mcp__firefox__build_firefox"
FIREFOX_TOOLS = [BUILD_TOOL]

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
