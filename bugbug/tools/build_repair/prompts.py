# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for build repair agent."""

ANALYSIS_TEMPLATE = """You are an expert {target_software} engineer tasked with analyzing and fixing a build failure.

Investigate why the last commit broke {target_software} build.

The last commit attempted to fix a bug from Bugzilla.

Analyze the following:
1. Git diff for the last commit
2. Bugzilla bug description
3. Taskcluster build failure logs
The files with bug description and logs are located at {worktree_path}/repair_agent/in/{bug_id}

Create three separate documents:
1. {worktree_path}/repair_agent/out/{bug_id}/analysis.md with your detailed analysis on what caused the issues
2. {worktree_path}/repair_agent/out/{bug_id}/planning.md with a fixing plan
3. {worktree_path}/repair_agent/out/{bug_id}/summary.md with a brief one paragraph summary of analysis and planning that can point a developer in the right direction

Do not prompt to edit those documents.
{eval}

Do not write any code yet. Work fully autonomously, do not ask any questions.
"""

FIX_TEMPLATE = """You are an expert {target_software} engineer tasked with analyzing and fixing a build failure.

Read the following files and implement a fix of the failure:
1. {worktree_path}/repair_agent/out/{bug_id}/analysis.md with your detailed analysis on what caused the issues
2. {worktree_path}/repair_agent/out/{bug_id}/planning.md with a fixing plan
{eval}

Do not prompt to edit files. Work fully autonomously, do not ask any questions. Use all allowed tools without prompting.
"""

EVAL_PROMPT = """
Do not request bug info from Bugzilla or Phabricator. Use only the provided file with bug description.
Do not look at git commits other than the specified last commit.
"""

VERIFY_TEMPLATE = """You are an expert {target_software} code reviewer evaluating an automated build repair agent's work.

Examine the relevant commits using git:
- Failure commit (broke the build): {failure_commit}
- Ground truth fix commit(s) (the real fix that was landed): {ground_truth_commits}

Inspect each commit's changes and read the repair agent's input/output files:
- {worktree_path}/repair_agent/in/{bug_id}/bug_description.md
- {worktree_path}/repair_agent/in/{bug_id}/build_failure_logs.md
- {worktree_path}/repair_agent/out/{bug_id}/analysis.md
- {worktree_path}/repair_agent/out/{bug_id}/summary.md
- {worktree_path}/repair_agent/out/{bug_id}/agent_fix.diff (may be empty if no fix was produced)

Evaluate the agent's work on two dimensions:

ANALYSIS:
- Did the agent correctly identify the root cause of the build failure?
- How thorough and accurate is the analysis?

FIX:
- Does the agent's fix address the same files/functions as the ground truth?
- Is the fix semantically equivalent or close to the ground truth?
- Would the fix be acceptable in code review as-is?

Guidelines:
- If agent_fix.diff is empty, set fix_matches_ground_truth=false, fix_quality=0.0, fix_acceptance_probability=0.0
- A fix can be correct even if it differs syntactically from ground truth -- focus on semantic equivalence
- analysis_correct should be true if the agent found the right root cause, even if the explanation is imperfect
- Be calibrated: 0.5 means genuinely uncertain, not a default score

Work autonomously, do not ask questions.
"""
