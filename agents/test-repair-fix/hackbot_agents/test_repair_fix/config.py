# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Model and tool allowlist for the test-repair-fix agent."""

FIX_MODEL = "claude-opus-5-5"

# The verdict has already reached the sheriffs, so the patch can take the time to
# be built and run.
SKIP_FIREFOX_BUILD = False

# Recordable action types the agent may take, by dotted id. Submitting the patch
# for review is the only one: the author reviews it in the Hackbot UI and then in
# Phabricator. It needs a bug to file the revision against, so it is wired only
# when the culprit's bug is known (see run_fix).
ENABLED_ACTION_TYPES = ["phabricator.submit_patch"]

# Only the build tool: evaluate_testcase / evaluate_js_shell do not run CI's
# harnesses, so the agent runs the failing test itself with mach over Bash.
BUILD_TOOL = "mcp__firefox__build_firefox"
FIREFOX_TOOLS = [BUILD_TOOL]

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
