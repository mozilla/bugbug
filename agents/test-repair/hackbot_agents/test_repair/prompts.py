# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the test-repair agent."""

MAX_TESTS_PER_GROUP = 100
MAX_CANDIDATE_COMMITS = 5

ANALYSIS_TEMPLATE = """\
You are investigating a failing Firefox CI test to find the commit that broke it,
or to establish that it is a known intermittent.

Failing test groups (manifests) and the tests that failed in each:
{failing_tests}
Test harness: {harness}
Test platform: {platform}
Test task: {label}
The label pins the build type, variant and chunk, so the failure may be specific
to this configuration rather than to the platform.

The source tree is at {source_repo} (your working directory), checked out at the
failure commit {failure_commit}. Scratch and log paths are outside it. Search it
with `git grep`, never `grep -r`, which hits the Bash timeout on a tree this big.
{candidate_intro}
{last_green_line}{known_intermittents_line}
Failure logs (start with the sanitized failures file, fall back to the full log):
{failure_logs}

Steps:
1. Read the sanitized failure lines to see exactly how the test failed.
2. Enumerate the candidates with `git log --oneline {commit_range}`. Path
   filtering on the failing test and the source it exercises tells you what to
   `git show` first, but never clears anyone: build config, shared headers,
   toolchain bumps, manifest and harness changes, and changes that only shift
   timing all break tests they do not touch. Work through the rest of the list
   before concluding nothing explains the failure.
3. Search Bugzilla for an intermittent bug tracking this failure -- the test name,
   or its file name plus "intermittent", finds most, including per-test "single
   tracking bug" entries. If one matches, the classification is "intermittent" and
   its id goes in "intermittent_bug", however suspicious a commit looks. Search
   for the bug the culprit landed under too, for "culprit_bug".
4. Check whether the culprit sits in a stack: adjacent commits sharing a
   `Bug NNNNNN` subject are one stack. If commits sit on top of the culprit, the
   whole stack has to be backed out -- name it in summary.md, oldest sha first.
   "culprit_commit" stays the single commit that introduced the regression.
5. Write to {scratch_out}:
   - summary.md: 2-3 sentences of plain prose -- no headings, lists or code
     blocks. It is posted verbatim to Slack and has to answer "do I back this
     out?" at a glance. Open with the action -- back out <sha>, do not back out,
     or retrigger -- and the one fact that settles it, then say what failed.
   - analysis.md: the developer's reference, readable in under a minute. Under 50
     lines, in exactly these sections, each a short paragraph or a few bullets:
       ## Verdict -- the action, the culprit and its bug, the confidence
       ## Failure -- how the test fails, with the log line that shows it
       ## Cause -- the hunk in the culprit that produces that failure
       ## Ruled out -- a line per alternative you seriously weighed; drop the
          section when there was none
     Quote only what decides something. Do not narrate the searches you ran, do
     not restate a diff you already pointed at, and do not add a section to
     report that a check passed.
   - verdict.json: "classification" ("regression" or "intermittent"),
     "culprit_commit" (full sha from the range, or null), "candidate_commits" (up
     to {max_candidates} full shas, most to least likely, whenever no single
     culprit convinces you; empty when one does), "culprit_bug" (int or null),
     "intermittent_bug" (int or null), "recommendation" ("backout",
     "do_not_backout" or "rerun") and "confidence" (0.0-1.0).

"recommendation" is the sheriff's action, not advice for the developer. A genuine
regression is always "backout"; there is no "land a fix instead". Never suggest a
follow-up patch on top of the culprit, however small -- the related changes land
in one push, so "small enough to fix forward" is not a reason to skip the backout.

Use "rerun" when you cannot tell whether the failure is real and a retrigger would
settle it. Prefer it over blaming a commit you are unsure of.

Never guess a sha: one that is not in the range is discarded, and a wrong blame is
worse than none.

Do not edit any source files in this step.
"""

CANDIDATE_INTRO_COMPLETE = """\
The {span} commits in `{commit_range}` landed since this test was last green --
the culprit is one of them."""

CANDIDATE_INTRO_PARTIAL = """\
`{commit_range}` is the {span} most recent commits, and is NOT known to reach back
to a green run, so the culprit may predate it. If nothing in it plausibly caused
the failure, say so and leave "culprit_commit" null and "candidate_commits" empty
rather than naming the least implausible commit."""

LAST_GREEN_LINE = (
    "The test was last green at hg revision {last_green_revision} (not a git"
    " object; the base of the range above is its git equivalent).\n"
)

KNOWN_INTERMITTENTS_LINE = (
    "Treeherder matches these failure lines to these bugs: {bugs}. Check whether"
    " they explain this failure before blaming a commit.\n"
)

FIX_TEMPLATE = """\
Commit {culprit_commit} regressed the failing test(s). Propose a minimal patch
fixing the root cause you identified in {scratch_out}/analysis.md.

The recommendation stays "backout". The patch is advice for the commit's author to
squash into their existing patches and reland, so write it as a change to the
original patch, not a follow-up on top of it.

The source tree is at {source_repo} (your working directory). Search it with
`git grep`, never `grep -r`.

1. Make the smallest change that addresses the root cause.
{verify_step}
3. Append a "## Patch" section to {scratch_out}/analysis.md, under 10 lines: the
   files it touches, the root cause it addresses, and whether it was verified.
   The diff is attached, so do not walk through it.
4. Add at most one sentence to {scratch_out}/summary.md saying a patch is
   proposed and whether it is verified. Leave the rest of it alone.
5. Update {scratch_out}/verdict.json in place, preserving its existing keys: set
   "proposed_patch" to true if you made a fix, and leave "recommendation" as
   "backout".
"""

VERIFY_LOCAL = """\
2. Build with the build_firefox tool, then run the failing test(s) over Bash with
   mach -- see the tree's AGENTS.md / CLAUDE.md for how it runs {harness} tests.
   They should pass."""

VERIFY_REMOTE = """\
2. Build with the build_firefox tool, then run the failing test(s) over Bash with
   mach -- see the tree's AGENTS.md / CLAUDE.md for how it runs {harness} tests.
   This container is Linux and the failure is on {platform}: a failure here is
   evidence the patch is wrong, but a pass proves nothing about {platform}. Report
   a pass as "not verified" and say so in the summary sentence below."""

VERIFY_SKIPPED = """\
2. Do not build, and do not try to run the test(s); no build tool is available.
   Check by reading instead that the patch compiles and addresses the root cause.
   State in the summary sentence below that it is unverified, neither built nor
   run."""

ENVIRONMENT_NOTE = """
   Environment: a Debian container with a virtual display and the build toolchain
   bootstrapped, so mach can build and run GUI harnesses. It is not CI's image, so
   a test that cannot run here at all is "could not verify", not a failure -- say
   which it was."""
