# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The Slack message a finished run sends to the channel.

Recorded as a ``slack.post_message`` action rather than posted from the run: it is
then visible in the hackbot UI before it lands, and the apply step delivers it at
most once (see ``hackbot_runtime.actions.slack``).

The wording is code rather than a model turn -- every run reaches a verdict worth
reporting, and sheriffs read these at a glance, so the fields and their order are
fixed. The agent's own prose is included as a single line so it cannot restructure
the message.
"""

from __future__ import annotations

from .agent import TestRepairResult
from .resolve import Investigation

TREEHERDER_PUSH = (
    "https://treeherder.mozilla.org/jobs?repo={project}&revision={revision}"
)
BUG_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}"

MAX_GROUPS = 3
MAX_SUMMARY_LENGTH = 300

_RECOMMENDATIONS = {
    "backout": "back out the culprit",
    "land_fix": "land a fix",
    "do_not_backout": "do not back out",
    "rerun": "retrigger the job",
}


def _link(url: str, label: str) -> str:
    return f"<{url}|{label}>"


def _bug_link(bug_id: int) -> str:
    return _link(BUG_URL.format(bug_id=bug_id), f"bug {bug_id}")


def _short(sha: str) -> str:
    return sha[:12]


def _one_line(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_SUMMARY_LENGTH:
        collapsed = collapsed[: MAX_SUMMARY_LENGTH - 1].rstrip() + "…"
    return collapsed


def _headline(result: TestRepairResult) -> str:
    advice = _RECOMMENDATIONS.get(result.recommendation, result.recommendation)
    return (
        f"*test-repair: {advice}* ({result.classification}, "
        f"confidence {result.confidence:.2f})"
    )


def _failure_line(investigation: Investigation) -> str:
    job = investigation.label or f"{investigation.harness} on {investigation.platform}"
    push = TREEHERDER_PUSH.format(
        project=investigation.project, revision=investigation.hg_revision
    )
    return f"`{job}` at {_link(push, _short(investigation.hg_revision))}"


def _failing_line(investigation: Investigation) -> str | None:
    groups = investigation.failing_groups[:MAX_GROUPS]
    if not groups:
        return None
    shown = ", ".join(f"{g.group} ({len(g.tests)} failed)" for g in groups)
    extra = len(investigation.failing_groups) - len(groups)
    return "Failing: " + shown + (f", +{extra} more" if extra > 0 else "")


def _blame_line(result: TestRepairResult) -> str:
    if result.culprit_commit:
        bug = f" ({_bug_link(result.culprit_bug)})" if result.culprit_bug else ""
        return f"Culprit: `{_short(result.culprit_commit)}`{bug}"
    if result.candidate_commits:
        candidates = ", ".join(f"`{_short(sha)}`" for sha in result.candidate_commits)
        return f"No single culprit; candidates: {candidates}"
    return "No culprit identified."


def build_message(result: TestRepairResult, investigation: Investigation) -> str:
    """Render the sheriff notification for a finished run."""
    lines = [_headline(result), _failure_line(investigation)]

    failing = _failing_line(investigation)
    if failing:
        lines.append(failing)

    lines.append(_blame_line(result))

    if result.classification == "intermittent" and result.intermittent_bug:
        lines.append(f"Known intermittent: {_bug_link(result.intermittent_bug)}")
    if result.proposed_patch:
        lines.append("A candidate fix patch is attached to the run.")
    if result.summary.strip():
        lines.append(_one_line(result.summary))

    return "\n".join(lines)
