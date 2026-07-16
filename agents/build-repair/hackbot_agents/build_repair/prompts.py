# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the build-repair agent."""

ANALYSIS_TEMPLATE = """You are an expert {target_software} engineer tasked with analyzing and fixing a build failure.

Investigate why the {target_software} build broke at commit {git_commit}. The source tree
is already checked out at that commit (your working directory).
{push_context}{bug_context}
Analyze the following:
1. The git diff of commit {git_commit} (use `git show {git_commit}`).
{bug_step}{logs_num}. The Taskcluster build failure logs. Each failing task has a sanitized log (only the ERROR -/FATAL - lines) and the full log. Start from the sanitized log -- it usually pinpoints the failing file and line. The full log can be tens of thousands of lines, so grep it for that file/line rather than reading it sequentially:
{failure_logs}

Create these documents:
1. {scratch_out}/analysis.md with your detailed analysis of what caused the failure
2. {scratch_out}/planning.md with a fixing plan
3. {scratch_out}/summary.md with a brief one-paragraph summary of the analysis and plan
   that can point a developer in the right direction
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

BLAME_STEP = """4. {scratch_out}/blame.json identifying the single commit that introduced the failure,
   as JSON: {{"blamed_commit": "<full git sha>", "reason": "<one sentence>"}}. Choose
   blamed_commit from the push commits listed above.
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

Edit the source files in the working directory to repair the build. A mozconfig
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
