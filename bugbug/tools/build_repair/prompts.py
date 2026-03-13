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
The files with bug description and logs are located at @repair_agent/in/{bug_id}

Create three separate documents:
1. repair_agent/out/{bug_id}/analysis.md with your detailed analysis on what caused the issues
2. repair_agent/out/{bug_id}/planning.md with a fixing plan
3. repair_agent/out/{bug_id}/summary.md with a brief one paragraph summary of analysis and planning that can point a developer in the right direction

Do not prompt to edit those documents.
{eval}

Do not write any code yet. Work fully autonomously, do not ask any questions. Think hard.
"""

FIX_TEMPLATE = """Read the following files and implement a fix of the failure:
1. repair_agent/out/{bug_id}/analysis.md with your detailed analysis on what caused the issues
2. repair_agent/out/{bug_id}/planning.md with a fixing plan
{eval}

Do not prompt to edit files. Work fully autonomously, do not ask any questions. Use all allowed tools without prompting.
"""

EVAL_PROMPT = """
Do not request bug info from Bugzilla or Phabricator. Use only the provided file with bug description.
Do not look at git commits other than the specified last commit.
"""
