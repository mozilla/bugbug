# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import date

ANALYSIS_MODEL = "claude-opus-4-6"
FIX_MODEL = "claude-opus-4-6"
DEFAULT_MAX_TURNS = 80
WORKTREE_BASE_DIR = "/tmp/build_repair_worktrees"
TRY_PUSH_TIMEOUT_SECONDS = 7200
TRY_PUSH_POLL_INTERVAL_SECONDS = 60
TREEHERDER_BASE_URL = "https://treeherder.mozilla.org"

FIREFOX_MCP_URL = "https://mcp-dev.moz.tools/mcp"

# Training data cutoff dates per model, for data contamination filtering.
# Examples with fix_commit_date before the cutoff may have been in training data.
# Source: https://platform.claude.com/docs/en/about-claude/models/overview
MODEL_CUTOFF_DATES = {
    "claude-opus-4-6": date(2025, 8, 1),
    "claude-sonnet-4-6": date(2026, 1, 1),
    "claude-haiku-4-5-20251001": date(2025, 7, 1),
    "claude-sonnet-4-5-20250929": date(2025, 7, 1),
    "claude-opus-4-5-20251101": date(2025, 8, 1),
    "claude-opus-4-1-20250805": date(2025, 3, 1),
    "claude-sonnet-4-20250514": date(2025, 3, 1),
    "claude-3-7-sonnet-20250219": date(2024, 11, 1),
    "claude-opus-4-20250514": date(2025, 3, 1),
}

CLAUDE_PERMISSIONS_CONFIG = {
    "permissions": {
        "allow": [
            "Edit(~/.mozbuild)",
            "Edit(~/.cache/uv)",
            "Bash(./mach build:*)",
            "Bash(./mach clobber:*)",
            "Bash(./mach configure:*)",
            "Bash(./mach run:*)",
            "Bash(./mach test:*)",
            "Bash(./mach wpt:*)",
            "Bash(./mach lint:*)",
            "Bash(./mach format:*)",
            "Bash(./mach clang-format:*)",
            "Bash(./mach try:*)",
            "Bash(./mach help:*)",
            "Bash(./mach vendor:*)",
            "Bash(./mach bootstrap:*)",
            "Bash(./mach artifact:*)",
            "Bash(clang++:*)",
            "Bash(rm:*)",
            "Bash(timeout:*)",
            "Bash(find:*)",
            "Bash(grep:*)",
            "Bash(tee:*)",
            "Bash(kill:*)",
            "Bash(searchfox-cli:*)",
            "Bash(treeherder-cli:*)",
            "Bash(jj:*)",
            "WebFetch(domain:firefox-source-docs.mozilla.org)",
            "WebFetch(domain:treeherder.mozilla.org)",
            "WebFetch(domain:searchfox.org)",
            "WebFetch(o1069899.ingest.sentry.io)",
        ],
        "deny": [],
        "additionalDirectories": [
            "~/.mozbuild",
            "~/.cache/uv/",
        ],
    },
    "sandbox": {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "network": {
            "allowLocalBinding": True,
        },
    },
}
