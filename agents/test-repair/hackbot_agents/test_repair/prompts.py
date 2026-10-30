# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the test-repair agent."""

SYSTEM_PROMPT_APPEND = """\
# Unattended run

You run in an isolated container as part of Mozilla's automated CI repair. No human
is watching or can answer, so do not ask questions or wait for replies: finish the
task and state the outcome in your final message. Do not propose good first bugs.

# Tools

`searchfox-cli`, `rg` and the `moz` MCP server are not available here; do not run
`./mach bootstrap` to get them. Search the tree with `git grep`, restricted to paths
where you can, never `grep -r`, which hits the Bash timeout on a tree this big. Use
the Bugzilla and Phabricator tools you were given.

# Working in the tree

- Stay in your working directory, the source tree: write scratch files by absolute
  path rather than `cd`-ing elsewhere. If a command reports "not a git repository",
  you have moved -- run `git -C <tree>` rather than hunting for the tree.
- Never read or cat a whole CI log; they run to six figures of lines. Read a window
  with `sed -n`, and clip the width as well as the line count with `| cut -c1-200`,
  because a single log line can be 10 KB.
"""

MAX_TESTS_PER_GROUP = 100
MAX_CANDIDATE_COMMITS = 5

ANALYSIS_TEMPLATE = """\
You are investigating a failing Firefox CI test so that sheriffs can decide
whether to back out a commit: find the commit that broke it, or establish that it
is a known intermittent. Your output is the recommendation and the root cause
that settles it. Fixing the regression is not your job.

Failing test groups (manifests) and the tests that failed in each:
{failing_tests}
Test harness: {harness}
Test platform: {platform}
Test task: {label}
The label pins the build type, variant and chunk, so the failure may be specific
to this configuration rather than to the platform.

The source tree is at {source_repo} (your working directory), checked out at the
failure commit {failure_commit}.
{candidate_intro}
{known_intermittents_line}Steps:
1. See exactly how the test failed. `treeherder-cli` queries Firefox CI directly,
   which is stronger evidence than the logs alone. This failure is on {project} at
   hg revision {hg_revision} -- an hg node, so pass it to treeherder-cli, never to
   git:
   `treeherder-cli {hg_revision} --repo {project} --filter '{label}'
   --include-intermittent --fetch-logs --pattern 'TEST-UNEXPECTED-'
   --cache-dir {scratch_out}/logs | head -100`
   Anchor short patterns on a word boundary: bare `ERROR -` also matches inside
   `-DHAVE_STRERROR -D...`. Each hit prints as `live_backing_log:<line>` and the
   full logs stay under {scratch_out}/logs, where those line numbers apply, so read
   a window around one to get the assertion, stack or diff that follows:
   `sed -n '5890,5930p' {scratch_out}/logs/job_<id>/live_backing_log.log | cut -c1-200`
   Two windows are usually enough.
   Two things can stop that command finding the job, and both are recoverable:
   - Treeherder sometimes returns a malformed response ("error decoding response
     body"). Retry the same command once; it usually succeeds.
   - "filter matched no jobs on this push" means the label did not match: check it
     with `--match-filter all --json`, which lists every job on the push. A match
     with no failures usually means `--include-intermittent` was missing.
   Once the retry has also failed there is nothing to analyse. Write what you ran
   and what it reported to {scratch_out}/error.txt and stop: do not write the other
   documents, guess a culprit, or reason from the diffs alone. Do not fetch the
   artifact from Taskcluster yourself either: after a retry or rerun the latest
   artifact can be a passing run's log, so a wrong log is worse than none. The run
   is meant to fail here.
2. Place the failure in CI history before reading any diff. The same command
   answers every CI question; the revisions it prints are the ones to pass back.
   - `--group-history <manifest> --lookback 150` for each failing manifest listed
     above: one line per push with how many tasks passed or failed the manifest,
     and a header naming the first failing and last passing push. Chunks are
     scheduled per manifest, so a green job does not mean the manifest ran; this
     is the only reliable timeline. `{commit_range}` is roughly the last 20
     pushes. If the last pass is older than the oldest push it covers, or the
     header says the failure predates the window, the culprit is not in this push:
     "classification" is "regression", "culprit_commit" null, "candidate_commits"
     empty, "recommendation" "do_not_backout", and summary.md names the push the
     failure started in. Do not hunt for a culprit outside the range.
   - `--similar-history <job id>` -- this job's recent pass rate, with revision,
     date and classification per push. Passes and failures alternating across
     pushes point at an intermittent; a run of passes ending at this push points
     at a regression.
   - `--lookback 20 --suspects --test '<test file>'` -- the push window the test
     started failing in, and the commits in it.
   - `--compare <revision>` -- whether the failure is new relative to a push that
     `--group-history` or `--similar-history` printed. `--context <N>` lists the
     pushes either side of this one.
   Every revision you pass has to be a real hg revision that treeherder-cli
   printed; it rejects git shas and placeholders with "No push found for
   revision". Never query the Treeherder API, hg.mozilla.org or the pushlog
   yourself, and do not read `--help`: if treeherder-cli cannot answer a question,
   say so in analysis.md and move on. The default markdown output is the compact
   one -- only add `--json` when you will parse it. Always pass `--filter` and
   pipe through `head`: unfiltered, one push can print hundreds of megabytes
   straight into your context. When output is cut off, redirect it to a file under
   {scratch_out} and grep that rather than re-running with a bigger `head`. Never
   pass `--watch` or `--stream-failures` -- they block until CI finishes.
3. Only when step 2 puts the first failure inside this range, enumerate the
   candidates with `git log --format='%h %ci %s' {commit_range}` once: the dates
   line up with the push times treeherder-cli printed, which tells you which
   commits landed in the candidate window. Path filtering on the failing test and
   the source it exercises tells you what to `git show` first, but never clears
   anyone: build config, shared headers, toolchain bumps, manifest and harness
   changes, and changes that only shift timing all break tests they do not touch.
   Read the diffs of the commits in the window before concluding nothing explains
   the failure; do not read diffs of commits that landed before the last green
   push.
4. Search Bugzilla for an intermittent bug tracking this failure -- the test name,
   or its file name plus "intermittent", finds most, including per-test "single
   tracking bug" entries. Search on the bare file name; a quicksearch on the full
   path often returns nothing. If one matches, the classification is
   "intermittent" and its id goes in "intermittent_bug", however suspicious a
   commit looks.
5. Check whether the culprit sits in a stack: adjacent commits sharing a
   `Bug NNNNNN` subject are one stack. If commits sit on top of the culprit, the
   whole stack has to be backed out -- name it in summary.md, oldest sha first.
   "culprit_commit" stays the single commit that introduced the regression.
6. Stop as soon as one of these holds, and write the verdict without gathering
   further confirmation:
   - step 2 shows the failure predates this range;
   - an intermittent bug matches and the job history alternates between pass and
     fail with no related commit in between;
   - a commit in the window changes the failing test, its manifest or the code the
     failing assertion exercises, and the manifest passed on the push before it.
   Budget: about 15 tool calls in total, and at most five treeherder-cli calls
   after the first. Spending more means the evidence is not there; prefer "rerun"
   over another round of searching.
7. Write summary.md, analysis.md and verdict.json to {scratch_out} in one Bash
   command, with a heredoc per file:
   - summary.md: 2-3 sentences of plain prose -- no headings, lists or code
     blocks. It is posted verbatim to Slack and has to answer "do I back this
     out?" at a glance. Open with the action -- back out <sha>, do not back out,
     or retrigger -- and the one fact that settles it, then say what failed.
   - analysis.md: the brief technical analysis a sheriff reads to trust the
     recommendation, readable in under a minute. Under 30 lines, in exactly these
     sections, each a short paragraph or a few bullets:
       ## Verdict -- the action, the culprit, the confidence
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
     culprit convinces you; empty when one does), "intermittent_bug" (int or
     null), "recommendation" ("backout",
     "do_not_backout" or "rerun") and "confidence" (0.0-1.0).
{fix_handoff}
"recommendation" is the sheriff's action. A genuine regression is always
"backout"; there is no "land a fix instead", and "small enough to fix forward" is
not a reason to skip the backout.

Use "rerun" when you cannot tell whether the failure is real and a retrigger would
settle it. Prefer it over blaming a commit you are unsure of.

Never guess a sha: one that is not in the range is discarded, and a wrong blame is
worse than none.

Do not edit any source files.
"""

CANDIDATE_INTRO = """\
`{commit_range}` is the {span} most recent commits. It is not known to reach back
to a green run, so the culprit may predate it -- `--group-history` below settles that.
If nothing in it plausibly caused the failure, say so and leave "culprit_commit"
null and "candidate_commits" empty rather than naming the least implausible
commit."""

KNOWN_INTERMITTENTS_LINE = (
    "Treeherder matches these failure lines to these bugs: {bugs}. Check whether"
    " they explain this failure before blaming a commit.\n"
)

FIX_HANDOFF_STEP = """\
8. Once the documents are written, call the `agents_start_run` action with agent
   "{agent}" if "culprit_commit" names the culprit. That agent fixes the culprit
   for its author, starting from your analysis; do not plan or attempt the fix
   yourself. Call it at most once.
"""
