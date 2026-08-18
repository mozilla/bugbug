# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The Slack message a finished run sends to the channel.

Recorded as a ``slack.post_message`` action rather than posted from the run: it is
then visible in the hackbot UI before it lands, and the apply step delivers it at
most once (see ``hackbot_runtime.actions.slack``).

Only verdicts a sheriff acts on are posted -- see :func:`sheriff_action_required`.

A few lines of context, then the verdict in full. Every identifier a sheriff would
otherwise have to look up -- revisions, task, bug, run -- is a link, the way the
pulse listener's email does it
(``services/hackbot-pulse-listener/app/notify.py``); unlike the email this stays
short enough to read in a channel, since the run holds the detail. The verdict is
what a sheriff acts on, so it is never truncated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hackbot_runtime.actions.slack import HACKBOT_UI_URL

from .agent import TestRepairResult
from .resolve import Investigation

GIT_COMMIT_URL = "https://github.com/mozilla-firefox/firefox/commit/{sha}"
HG_REV_URL = "https://hg.mozilla.org/mozilla-unified/rev/{rev}"
TASK_URL = "https://firefox-ci-tc.services.mozilla.com/tasks/{task_id}"
TREEHERDER_JOB_URL = (
    "https://treeherder.mozilla.org/#/jobs"
    "?repo={project}&revision={revision}&selectedTaskRun={task_id}"
)
BUG_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}"
RUN_URL = HACKBOT_UI_URL.rstrip("/") + "/runs/{run_id}"

_RECOMMENDATIONS = {
    "backout": "BACK OUT the culprit",
    "do_not_backout": "DO NOT back out (intermittent)",
    "rerun": "RETRIGGER the job",
}


def sheriff_action_required(result: TestRepairResult) -> bool:
    """Whether the verdict is one a sheriff has to act on.

    A known intermittent asks for nothing -- no backout, no retrigger -- and it is the
    majority verdict, so posting those is pure noise. ``rerun`` is not one of them: an
    intermittent the agent could not confirm still asks for a retrigger.
    """
    return not (
        result.classification == "intermittent"
        and result.recommendation == "do_not_backout"
    )


def _link(url: str, label: str) -> str:
    return f"<{url}|{label}>"


def _commit_link(sha: str) -> str:
    return _link(GIT_COMMIT_URL.format(sha=sha), f"github {sha[:12]}")


def _hg_link(rev: str) -> str:
    return _link(HG_REV_URL.format(rev=rev), f"hg {rev[:12]}")


def _bug_link(bug_id: int) -> str:
    return _link(BUG_URL.format(bug_id=bug_id), f"bug {bug_id}")


def resolve_culprit_author(source_repo: Path, sha: str | None) -> str | None:
    """The culprit's author email, so the notification names who to ask."""
    if not sha:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "show", "-s", "--format=%ae", sha],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return proc.stdout.strip() or None


def _failing_line(investigation: Investigation) -> str:
    groups = (
        ", ".join(f"`{group.group}`" for group in investigation.failing_groups)
        or "tests not resolved"
    )
    job = investigation.label or f"{investigation.harness} on {investigation.platform}"
    return f"Failing: {groups} in `{job}`"


def _jobs_line(investigation: Investigation, task_id: str) -> str:
    treeherder = _link(
        TREEHERDER_JOB_URL.format(
            project=investigation.project,
            revision=investigation.hg_revision,
            task_id=task_id,
        ),
        "Treeherder",
    )
    task = _link(TASK_URL.format(task_id=task_id), f"Taskcluster {task_id}")
    return f"Jobs: {treeherder}, {task}"


def _push_line(investigation: Investigation) -> str:
    line = (
        f"Push: {investigation.project} {_hg_link(investigation.hg_revision)}"
        f" / {_commit_link(investigation.failure_commit)}"
    )
    if investigation.last_green_revision:
        line += f", last green {_hg_link(investigation.last_green_revision)}"
    return line


def _culprit_line(result: TestRepairResult, culprit_author: str | None) -> str:
    if result.culprit_commit:
        author = f" by {culprit_author}" if culprit_author else ""
        line = f"Culprit: {_commit_link(result.culprit_commit)}{author}"
    elif result.candidate_commits:
        candidates = ", ".join(_commit_link(sha) for sha in result.candidate_commits)
        line = f"Culprit: not narrowed down, candidates {candidates}"
    else:
        line = "Culprit: none identified"

    bug = result.culprit_bug or result.intermittent_bug
    if bug:
        line += f" ({_bug_link(bug)})"
    return line


def _patch_lines(result: TestRepairResult) -> list[str]:
    """Who the attached patch is for, so it is not read as an alternative action."""
    if not result.proposed_patch:
        return []
    return [
        "Patch attached for the author: squash it into the existing patches and"
        " reland, rather than landing it as a follow-up. The backout still stands."
    ]


def build_message(
    result: TestRepairResult,
    investigation: Investigation,
    *,
    task_id: str,
    run_id: str,
    culprit_author: str | None = None,
) -> str:
    """Render the notification for a finished run."""
    recommendation = _RECOMMENDATIONS.get(result.recommendation, result.recommendation)
    lines = [
        f"*test-repair: {recommendation}*"
        f" ({result.classification}, confidence {result.confidence})",
        _failing_line(investigation),
        _jobs_line(investigation, task_id),
        _push_line(investigation),
        _culprit_line(result, culprit_author),
        *_patch_lines(result),
        _link(RUN_URL.format(run_id=run_id), "Hackbot run details"),
    ]
    if result.summary.strip():
        lines += ["", result.summary.strip()]
    return "\n".join(lines)
