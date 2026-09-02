# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the build-repair agent."""

ANALYSIS_TEMPLATE = """You are an expert {target_software} engineer tasked with analyzing and fixing a build failure.

Investigate why the {target_software} build broke at commit {git_commit}. The source
tree is at {source_repo} (your working directory), checked out at that commit. Stay
in it: write scratch files by absolute path rather than `cd`-ing elsewhere. If a
command reports "not a git repository" you have moved -- run `git -C {source_repo}`
rather than hunting for the tree.
{push_context}{bug_context}
Analyze the following:
1. The git diff of commit {git_commit} (use `git show {git_commit}`).
{bug_step}{logs_num}.{treeherder_step}
Create these documents:
1. {scratch_out}/analysis.md -- the developer's reference, readable in under a
   minute. Under 40 lines, in exactly these sections, each a short paragraph or a
   few bullets:
     ## Verdict -- which commit broke the build, or that none of them did
     ## Error -- what the build reports, with the line that shows it
     ## Cause -- the change that produces that error
     ## Fix -- what to change
   Quote only what decides something. Do not restate a diff you already pointed
   at, and do not narrate the steps you took to get here.
2. {scratch_out}/planning.md with the fix as a short numbered list of edits
3. {scratch_out}/summary.md -- 2-3 sentences of plain prose, no headings or lists.
   Open with whether a commit in this push broke the build and which one, then
   give the error and the fix in a clause each.
{blame_step}
Do not prompt to edit those documents. Do not write any code yet. Work fully
autonomously and do not ask any questions.
"""

PUSH_CONTEXT = """
This commit landed in the same push as the commits below. Any of them may have
introduced the failure -- the checked-out commit is not necessarily the culprit:
{commit_lines}
Inspect each commit (`git show <commit>`) and correlate with the failure logs to
determine which single commit introduced the build failure.
"""

PUSH_COMMIT_LINE = "- {commit}"

TREEHERDER_STEP = r"""\
   The build failure logs, via `treeherder-cli`. This push is {project} revision
   {hg_revision}, and the failing task is '{task_name}'. Start with the error lines:
   `treeherder-cli {hg_revision} --repo {project} --filter '{task_name}'
   --include-intermittent --fetch-logs --pattern '\b(?:ERROR|FATAL) -'
   --cache-dir {scratch_out}/logs | head -100`
   Anchor short patterns on a word boundary: bare `ERROR -` also matches inside
   `-DHAVE_STRERROR -D...` and buries the real errors in compiler command lines.
   Each hit prints as `live_backing_log:<line>` and the full logs stay under
   {scratch_out}/logs, where those line numbers apply, so read a window around one
   to get the whole diagnostic:
   `sed -n '165280,165320p' {scratch_out}/logs/job_<id>/live_backing_log.log | cut -c1-200`
   An `ERROR -` line is usually only the first line of a compiler error -- the
   offending source and the `^` caret follow it. Never read or cat a whole log;
   they run to six figures of lines.
   Clip the width as well as the line count: log lines run to thousands of
   characters, so append `| cut -c1-200` to any grep or sed over a log -- 40 wpt
   lines alone came to 38 KB without it.
   Two things can stop that command finding the job, and both are recoverable:
   - Treeherder sometimes returns a malformed response ("error decoding response
     body"). Retry the same command once; it usually succeeds.
   - "N passing jobs ... no failures found" almost always means
     `--include-intermittent` was missing: a failure a sheriff has already
     classified is hidden without it, and that covers most of them. Check the flag
     is there and re-run.
   Once the retry has also failed there is nothing to analyse. Write what you ran
   and what it reported to {scratch_out}/error.txt and stop: do not write the other
   documents, guess a cause, or propose a fix from the diff alone. Do not fetch the
   artifact from Taskcluster yourself either: after a retry or rerun the latest
   artifact can be a passing run's log, so a wrong log is worse than none. The run
   is meant to fail here.

   The same command answers CI questions about the push. `--compare <revision>`
   says whether the failure is new here or was already failing earlier;
   `--lookback 50 --suspects` finds the push window a failure started in;
   `--similar-history <job id>` gives a job's recent pass rate, which separates a
   real bustage from infrastructure flakiness.

   Every revision you pass has to be a real hg revision that treeherder-cli
   printed. It rejects anything else with "No push found for revision" -- both the
   git shas of the push commits above and placeholders like `parent`. To compare
   against a neighbouring push, get its revision from `--context 3` first, which
   lists the pushes either side of this one, then pass that to `--compare`.

   `--help` lists the rest. The default markdown output is the compact one -- only
   add `--json` when you will parse it. To see more of a log widen `--pattern`.
   Always pass `--filter` and pipe through `head`: unfiltered, one push can print
   hundreds of megabytes straight into your context. Never pass `--watch` or
   `--stream-failures` -- they block until CI finishes.
"""


TREEHERDER_STEP_NO_PUSH = """\
   This push could not be resolved on Treeherder, so the failure logs cannot be
   reached. Write that to {scratch_out}/error.txt and stop: do not write the other
   documents or propose a fix from the diff alone.
"""


BLAME_STEP = """4. {scratch_out}/blame.json naming the commit that introduced the failure, as JSON:
   {{"blamed_commit": "<full git sha>", "reason": "<one sentence>"}}. Use one of the
   push commits listed above when there are several, otherwise the checked-out
   commit. Set "blamed_commit" to null if none of them caused the failure -- it is
   infrastructure, a toolchain or fetch problem, or it already failed before this
   push -- rather than naming the least implausible commit.
"""

BUG_CONTEXT = "\nThe commit attempted to fix Bugzilla bug {bug_id}.\n"

BUG_ANALYSIS_STEP = (
    "2. The Bugzilla bug: fetch bug {bug_id}'s description and comments with the "
    "`mcp__bugzilla__get_bugs` tool (ids=[{bug_id}], include_comments=true). If "
    "it returns an error, note it and continue with the diff and logs.\n"
)

FIX_TEMPLATE = """You are an expert {target_software} engineer tasked with fixing a build failure.

Read your earlier analysis and implement the fix directly in the source tree:
1. {scratch_out}/analysis.md -- your analysis of what caused the failure
2. {scratch_out}/planning.md -- your fixing plan

Edit the source files in {source_repo} (your working directory) to repair the build.
Editing: use Edit on a file that already exists -- Write refuses until the file has
been read, which costs a turn. To see how a commit handled comparable files, run
`git show <sha> -- <dir>` rather than guessing a sibling's name.

Working in this tree: review your own edits with `git diff --stat` and `git diff --
<path>`, never `git status` -- after a build the objdir adds millions of untracked
files and the output runs to tens of MB. Logs already fetched sit under
{scratch_out}/logs; when you grep or sed one, cap the width as well as the line
count (`| head -40 | cut -c1-200`), because a single build-log line can be 10 KB.
 A mozconfig
that mirrors the failing CI configuration (release milestone, warnings-as-errors)
is already set up. Verify the fix compiles with the build_firefox tool, passing
the directory of the file you changed as `target` (e.g. 'docshell/base') for a
fast, focused build -- prefer this over a full tree build. If the build reports a
missing toolchain (e.g. rustc or clang), run the bootstrap_firefox tool once and
then build again. Verify via the build_firefox tool rather than a raw `./mach
build` so the build result is recorded.
{try_push}

Do not prompt to edit files. Work fully autonomously, do not ask any questions.
Use all allowed tools without prompting.
"""

TRY_PUSH_INSTRUCTIONS = """
Once the fix builds locally, validate it on CI: call the submit_try_push tool with the
failing task name ('{task_name}') to push to the try server and report the build result.
"""
