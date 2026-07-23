# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the test-repair agent."""

ANALYSIS_TEMPLATE = """\
You are investigating a failing Firefox CI test to find the commit that broke it.
Treat this as a genuine regression: a commit that landed since the test was last
green introduced the failure.

Failing test groups (manifests) and a representative failing test in each:
{failing_tests}
Test harness: {harness}

The source tree is checked out at the failure commit {failure_commit}. The commits
that landed since this test was last green are listed below, newest first -- the
culprit is one of these:
{commit_lines}
{last_green_line}
Failure logs (start with the sanitized failures file; fall back to the full log):
{failure_logs}

Do the following:
1. Read the sanitized failure lines to understand exactly how the test failed.
2. Use `git show <commit>` on the candidate commits above to inspect their diffs
   and identify the single commit that most plausibly introduced the failure. You
   may search Bugzilla for a related bug.
3. Write these files to {scratch_out}:
   - summary.md: a short (2-4 sentence) verdict.
   - analysis.md: the detailed reasoning, with evidence from the logs and diffs.
   - verdict.json: an object with keys "culprit_commit" (a full sha from the
     candidates above, or null if none is convincing), "culprit_bug" (integer or
     null), "recommendation" ("backout" or "land_fix") and "confidence"
     (0.0-1.0).

Do not edit any source files in this step.
"""

LAST_GREEN_LINE = "The test was last green at revision {last_green_revision}.\n"

FIX_TEMPLATE = """\
You determined that commit {culprit_commit} regressed the failing test(s).
Propose a minimal source patch that fixes the failure.

1. Make the smallest change that addresses the root cause you identified in
   {scratch_out}/analysis.md.
2. If practical, verify the fix with the Firefox MCP tools (build_firefox /
   evaluate_testcase).
3. Update {scratch_out}/verdict.json: set "proposed_patch" to true if you made a
   fix, and set "recommendation" to "land_fix" only if you are confident in the
   fix; otherwise keep "backout".

Keep the patch minimal and focused on the regression.
"""
