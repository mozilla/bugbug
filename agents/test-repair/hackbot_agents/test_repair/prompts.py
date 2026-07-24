# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the test-repair agent."""

ANALYSIS_TEMPLATE = """\
You are investigating a failing Firefox CI test to find the commit that broke it.
The CI listener already filtered out known intermittents, so treat this as a
genuine regression unless the logs clearly show otherwise.

Failing test groups (manifests) and a representative failing test in each:
{failing_tests}
Test harness: {harness}

The source tree is checked out at the failure commit {failure_commit}.
{candidate_intro}
{last_green_line}
Failure logs (start with the sanitized failures file; fall back to the full log):
{failure_logs}

Do the following:
1. Read the sanitized failure lines to understand exactly how the test failed.
2. Enumerate the candidates with `git log --oneline {commit_range}`. Narrow them
   before reading diffs -- `git log --oneline {commit_range} -- <path>` on the
   failing test's directory and on the source it exercises is usually enough to
   get to a handful. Then `git show <commit>` those to identify the single commit
   that most plausibly introduced the failure. You may search Bugzilla for a
   related bug.
3. Write these files to {scratch_out}:
   - summary.md: a short (2-4 sentence) verdict.
   - analysis.md: the detailed reasoning, with evidence from the logs and diffs.
   - verdict.json: an object with keys "classification" ("regression" or
     "intermittent"), "culprit_commit" (a full sha copied verbatim from the
     candidate list above, or null if none is convincing), "culprit_bug" (integer
     or null), "intermittent_bug" (integer or null; the bug already tracking this
     intermittent, if you found one), "recommendation" ("backout", "land_fix" or
     "do_not_backout") and "confidence" (0.0-1.0).

Never guess a sha: one that is not a real commit in the range above is discarded,
and a wrong blame is worse than no blame. Use null when nothing convinces you.

Do not edit any source files in this step.
"""

# Picked by resolve.CommitRange.complete: how firmly the prompt may assert that
# the culprit is inside the range.
CANDIDATE_INTRO_COMPLETE = """\
The {span} commits in `{commit_range}` landed since this test was last green --
the culprit is one of them."""

CANDIDATE_INTRO_PARTIAL = """\
`{commit_range}` is the {span} most recent commits. This range is NOT known to
reach back to a run where the test was green, so the culprit may predate it -- if
nothing in it plausibly caused the failure, say so in your analysis and set
"culprit_commit" to null."""

LAST_GREEN_LINE = "The test was last green at revision {last_green_revision}.\n"

FIX_TEMPLATE = """\
You determined that commit {culprit_commit} regressed the failing test(s).
Propose a minimal source patch that fixes the failure.

1. Make the smallest change that addresses the root cause you identified in
   {scratch_out}/analysis.md.
2. If practical, verify the fix with the Firefox MCP tools (build_firefox /
   evaluate_testcase). A mozconfig matching the failing CI build is already in
   place.
3. Update {scratch_out}/verdict.json in place, preserving every key it already
   has: set "proposed_patch" to true if you made a fix, and set "recommendation"
   to "land_fix" only if you are confident in the fix; otherwise keep "backout".

Keep the patch minimal and focused on the regression.
"""
