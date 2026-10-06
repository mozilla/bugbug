# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""What a finished run reports, in Slack and by email.

Both are recorded as actions rather than sent from the run: they are then visible
in the hackbot UI before they land, and the apply step delivers each at most once
(see ``hackbot_runtime.actions.slack`` / ``.email``).

Only verdicts a sheriff acts on go to the channel -- see
:func:`sheriff_action_required`. Every verdict is emailed, so the team can track
what the agent decided either way; the culprit's author is addressed too when there
is a patch for them to reland -- see :func:`recipients`.

Every identifier a recipient would otherwise have to look up -- revisions, task,
bug, run -- is a link. The Slack message stays short enough to read in a channel,
since the run holds the detail; the email carries the full analysis.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hackbot_runtime.actions.email import PATCH_PLACEHOLDER, demote_headings
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
# The email reaches developers as well as the team, so it states the sheriff's
# action without shouting it.
_EMAIL_RECOMMENDATIONS = {
    "backout": "sheriffs back out the culprit",
    "do_not_backout": "no backout",
    "rerun": "sheriffs retrigger the job",
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


def recipients(result: TestRepairResult, culprit_author: str | None) -> list[str]:
    """Who the verdict concerns individually.

    The culprit's author, when a patch was proposed for them to squash and reland.
    The team address is added apply-side.
    """
    if result.proposed_patch and culprit_author:
        return [culprit_author]
    return []


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
    return (
        f"Push: {investigation.project} {_hg_link(investigation.hg_revision)}"
        f" / {_commit_link(investigation.failure_commit)}"
    )


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


def _md_link(url: str, label: str) -> str:
    return f"[{label}]({url})"


def _groups_label(investigation: Investigation) -> str:
    """A one-line name for the run's failing groups, for the email subject."""
    if not investigation.failing_groups:
        return investigation.label or "unresolved tests"
    first, *rest = [group.group for group in investigation.failing_groups]
    return f"{first} (+{len(rest)} more)" if rest else first


def _already_actioned_banner(classification: str | None) -> list[str]:
    """Say up front that a sheriff has dealt with the tree, when they have."""
    if not classification:
        return []
    consequence = (
        ", usually a backout. The analysis and patch below are for the reland."
        if classification == "fixed by commit"
        else "; nothing more is needed on the tree."
    )
    return [
        f"> **A sheriff has already handled this** -- Treeherder classifies the job"
        f" as _{classification}_{consequence}",
        "",
    ]


def _headline(result: TestRepairResult, investigation: Investigation) -> str:
    """What happened, in a developer's words, for the subject line."""
    tests = _groups_label(investigation)
    if result.culprit_commit:
        patch = " - patch proposed" if result.proposed_patch else ""
        return f"{tests} regressed by {result.culprit_commit[:12]}{patch}"
    if result.classification == "intermittent":
        return f"Intermittent failure in {tests}"
    if result.recommendation == "rerun":
        return f"Unclear failure in {tests}, retrigger suggested"
    return f"Regression in {tests}, culprit not identified"


def _why_paragraph(result: TestRepairResult, culprit_author: str | None) -> list[str]:
    """Tell the author up front why they are on the email."""
    if not recipients(result, culprit_author):
        return []
    link = _md_link(
        GIT_COMMIT_URL.format(sha=result.culprit_commit),
        f"`{result.culprit_commit[:12]}`",
    )
    return [
        f"**{culprit_author}**, the agent believes your {link} broke the tests below."
        " The patch at the end is for your reland, not to land on its own.",
        "",
    ]


def _failing_bullet(investigation: Investigation, task_id: str) -> str:
    groups = (
        ", ".join(f"`{group.group}`" for group in investigation.failing_groups)
        or "not resolved"
    )
    job = investigation.label or f"{investigation.harness} on {investigation.platform}"
    treeherder = _md_link(
        TREEHERDER_JOB_URL.format(
            project=investigation.project,
            revision=investigation.hg_revision,
            task_id=task_id,
        ),
        "Treeherder",
    )
    task = _md_link(TASK_URL.format(task_id=task_id), "task")
    return f"- **Failing:** {groups} in `{job}` ({treeherder}, {task})"


def _push_bullet(investigation: Investigation) -> str:
    hg = _md_link(
        HG_REV_URL.format(rev=investigation.hg_revision),
        f"hg {investigation.hg_revision[:12]}",
    )
    git = _md_link(
        GIT_COMMIT_URL.format(sha=investigation.failure_commit),
        f"git {investigation.failure_commit[:12]}",
    )
    return f"- **Push:** {investigation.project} {hg} / {git}"


def _culprit_bullet(result: TestRepairResult, culprit_author: str | None) -> str:
    if result.culprit_commit:
        by = f" by {culprit_author}" if culprit_author else ""
        line = (
            "- **Culprit:** "
            + _md_link(
                GIT_COMMIT_URL.format(sha=result.culprit_commit),
                f"`{result.culprit_commit[:12]}`",
            )
            + by
        )
    elif result.candidate_commits:
        candidates = ", ".join(
            _md_link(GIT_COMMIT_URL.format(sha=sha), f"`{sha[:12]}`")
            for sha in result.candidate_commits
        )
        line = f"- **Culprit:** not narrowed down, candidates {candidates}"
    else:
        line = "- **Culprit:** none identified"
    bug = result.culprit_bug or result.intermittent_bug
    if bug:
        line += ", " + _md_link(BUG_URL.format(bug_id=bug), f"bug {bug}")
    return line


def _verdict_bullet(result: TestRepairResult, already_actioned: str | None) -> str:
    line = f"- **Verdict:** {result.classification}, confidence {result.confidence}"
    # Once a sheriff has acted, the recommendation is history.
    if not already_actioned:
        action = _EMAIL_RECOMMENDATIONS.get(
            result.recommendation, result.recommendation
        )
        line += f"; {action}"
    return line


def _reland_steps(
    bug_id: int | None, run_url: str, parent_revision: int | None
) -> list[str]:
    """How the author gets the pending revision into their own patch."""
    run_page = _md_link(run_url, "run page")
    if parent_revision is None:
        bug = _md_link(BUG_URL.format(bug_id=bug_id), f"bug {bug_id}")
        return [
            f"**Phabricator:** *Apply pending actions* on the {run_page} files this"
            f" patch as a WIP revision on {bug}. Apply it on top of your patch with"
            " `moz-phab patch D<new> --apply-to @`, squash, and `moz-phab submit`.",
        ]
    parent = f"D{parent_revision}"
    return [
        f"**Phabricator:** *Apply pending actions* on the {run_page} files this patch"
        f" as a child revision of {parent}. To reland:",
        "",
        "```",
        f"moz-phab patch {parent}",
        "moz-phab patch D<new> --apply-to @",
        "git rebase -i @~1   # squash the fix into your patch",
        "moz-phab patch D<next> --skip-dependencies   # each later patch in your stack",
        "moz-phab submit",
        "```",
    ]


def _analysis_section(result: TestRepairResult) -> list[str]:
    """The analysis when there is one, else the summary: they say the same thing."""
    if result.analysis.strip():
        return ["", "## Analysis", "", demote_headings(result.analysis)]
    if result.summary.strip():
        return ["", result.summary.strip()]
    return []


def build_email(
    result: TestRepairResult,
    investigation: Investigation,
    *,
    task_id: str,
    run_id: str,
    culprit_author: str | None = None,
    already_actioned: str | None = None,
    revision_pending: bool = False,
    parent_revision: int | None = None,
) -> tuple[str, str]:
    """The subject and markdown body of the verdict email.

    ``revision_pending`` means the run recorded a ``phabricator.submit_patch``
    action that is waiting for approval, so the email says how to apply it;
    ``parent_revision`` is the culprit's revision it stacks on, when known.
    """
    # In the subject too, so it can be skipped from the inbox.
    prefix = "[handled by sheriff] " if already_actioned else ""
    subject = (
        f"[test-repair] {prefix}{_headline(result, investigation)}"
        f" ({investigation.project})"
    )
    run_url = RUN_URL.format(run_id=run_id)

    lines = [
        *_already_actioned_banner(already_actioned),
        "# Test failure analysis",
        "",
        *_why_paragraph(result, culprit_author),
        _failing_bullet(investigation, task_id),
        _push_bullet(investigation),
        _culprit_bullet(result, culprit_author),
        _verdict_bullet(result, already_actioned),
        f"- **Run:** {run_url}",
    ]
    lines += _analysis_section(result)
    if revision_pending:
        lines += ["", *_reland_steps(result.culprit_bug, run_url, parent_revision)]
    if result.proposed_patch:
        # The diff itself is substituted for the placeholder when the mail is sent,
        # from the same artifact it attaches.
        lines += ["", "## Proposed patch", "", "```diff", PATCH_PLACEHOLDER, "```"]
    return subject, "\n".join(lines)
