# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the test-repair agent."""

# Failing tests listed per group before the list is elided. High enough to list
# them all in practice; only a whole-manifest failure hits it, and there the count
# printed alongside carries the signal the names would.
MAX_TESTS_PER_GROUP = 100

# Candidate shas kept from the model's ranked list when it names no single culprit.
MAX_CANDIDATE_COMMITS = 5

ANALYSIS_TEMPLATE = """\
You are investigating a failing Firefox CI test to find the commit that broke it.
The CI listener already filtered out known intermittents, so treat this as a
genuine regression unless the logs clearly show otherwise.

Failing test groups (manifests) and the tests that failed in each:
{failing_tests}
Test harness: {harness}
Test platform: {platform}
Test task: {label}
The task label pins the build type, test variant and chunk, not just the OS -- a
failure can be specific to this exact configuration rather than to the platform.

The source tree is checked out at the failure commit {failure_commit}.
{candidate_intro}
{last_green_line}
Failure logs (start with the sanitized failures file; fall back to the full log):
{failure_logs}

Do the following:
1. Read the sanitized failure lines to understand exactly how the test failed.
2. Enumerate every candidate with `git log --oneline {commit_range}`. That full
   list is the set of possibilities; path filtering only decides what you read
   first, it does not clear anyone. `git log --oneline {commit_range} -- <path>`
   on the failing test's directory and on the source it exercises gets you to the
   likeliest few, so `git show` those first -- but plenty of real culprits touch
   neither: build config and mozconfig changes, shared headers, dependency and
   toolchain bumps, manifest or harness changes, and changes that only shift
   timing or memory elsewhere. If none of the path-matching commits explains the
   failure, go through the rest of the list before concluding that nothing does.
   You may search Bugzilla for a related bug.
3. Write these files to {scratch_out}:
   - summary.md: a short (2-4 sentence) verdict.
   - analysis.md: the detailed reasoning, with evidence from the logs and diffs.
   - verdict.json: an object with keys "classification" ("regression" or
     "intermittent"), "culprit_commit" (a full sha copied verbatim from the
     candidate list above, or null if none is convincing), "candidate_commits"
     (see below), "culprit_bug" (integer or null), "intermittent_bug" (integer or
     null; the bug already tracking this intermittent, if you found one),
     "recommendation" ("backout", "land_fix", "do_not_backout" or "rerun") and
     "confidence" (0.0-1.0).

Set "candidate_commits" whenever you are not confident in a single culprit: up to
{max_candidates} full shas from the range, most to least likely, so sheriffs can
retrigger just those instead of backfilling the whole range. Include the commits
you could not rule out, not only the one you like best; leave it as an empty list
when "culprit_commit" is certain, and still fill it in when "culprit_commit" is
null but some commits are more suspicious than others.

Use "rerun" when you cannot tell whether the failure is real -- infrastructure
noise, a timeout under load, or a failure no candidate plausibly explains -- and
a retrigger would settle it. Prefer it over blaming a commit you are unsure of.

Never guess a sha: any that is not a real commit in the range above is discarded,
in "culprit_commit" and in "candidate_commits" alike, and a wrong blame is worse
than no blame. Use null when nothing convinces you.

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
nothing in it plausibly caused the failure, say so in your analysis, set
"culprit_commit" to null and leave "candidate_commits" empty rather than naming
the least implausible commit in the range."""

# Explicitly an hg revision: everything else in the prompt is a git hash, and an
# unlabelled hg node just makes the agent try `git` commands that exit 128.
LAST_GREEN_LINE = (
    "The test was last green at hg revision {last_green_revision} (not a git"
    " object; the base of the range above is its git equivalent).\n"
)

FIX_TEMPLATE = """\
You determined that commit {culprit_commit} regressed the failing test(s).
Propose a minimal source patch that fixes the failure.

1. Make the smallest change that addresses the root cause you identified in
   {scratch_out}/analysis.md.
{verify_step}
3. Update {scratch_out}/verdict.json in place, preserving every key it already
   has: set "proposed_patch" to true if you made a fix, and set "recommendation"
   to "land_fix" only if you are confident in the fix; otherwise keep "backout".

Keep the patch minimal and focused on the regression.
"""

# The agent's container is Linux. Both steps run the test; they differ only in
# what a passing run is allowed to prove.
VERIFY_LOCAL = """\
2. Verify the fix: build with the build_firefox tool (a mozconfig matching the
   failing CI build is already in place), then run the failing test(s) yourself
   over Bash with mach -- consult the tree's own AGENTS.md / CLAUDE.md for how it
   runs {harness} tests. They should pass."""

VERIFY_REMOTE = """\
2. Build with the build_firefox tool (a mozconfig matching the failing CI build is
   already in place), then still run the failing test(s) over Bash with mach --
   consult the tree's own AGENTS.md / CLAUDE.md for how it runs {harness} tests.
   Read the outcome asymmetrically: this container is Linux and the failure is on
   {platform}, so a failure here is real evidence the patch is wrong or breaks
   Linux, while a pass proves nothing about {platform} -- the test may even be
   skipped or absent here. Report a pass as "not verified" rather than as
   verification, and do not set "land_fix" on the strength of it alone."""

# The image ships Xvfb, Firefox's runtime libraries and fonts, and bootstrap has
# already run, so GUI harnesses are runnable -- but it is Debian, not CI's image.
ENVIRONMENT_NOTE = """
   Environment: a Debian container with a virtual display (DISPLAY is already set,
   Xvfb is running) and the build toolchain bootstrapped, so mach can build and
   run GUI harnesses. It is not CI's image though, and CI ran {platform}, so a
   test that cannot run here at all is "could not verify", not a failure -- say
   which it was."""
