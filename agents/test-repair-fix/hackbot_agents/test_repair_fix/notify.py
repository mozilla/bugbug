# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The email that hands a proposed patch to the culprit's author.

Recorded as an action, like the test-repair notifications, and copied to the team
apply-side. It is written for the developer: what is wrong with their patch, the
fix, how to reland it -- the backout itself is the test-repair run's to report.
"""

from hackbot_agents.test_repair.notify import (
    BUG_URL,
    GIT_COMMIT_URL,
    RUN_URL,
    failing_bullet,
    groups_label,
    jobs_bullet,
    md_link,
    push_bullet,
)
from hackbot_agents.test_repair.resolve import Investigation
from hackbot_runtime.actions.email import PATCH_PLACEHOLDER, demote_headings

from .agent import TestRepairFixResult


def recipients(
    result: TestRepairFixResult,
    culprit_author: str | None,
    classification: str | None = None,
) -> list[str]:
    """The culprit's author, when there is a patch for them to squash and reland."""
    # Treeherder can misclassify a regression as intermittent, so the patch may
    # still help, but a reland email for an intermittent confuses the author.
    if classification and "intermittent" in classification:
        return []
    if result.proposed_patch and culprit_author:
        return [culprit_author]
    return []


def _commit_md(sha: str) -> str:
    return md_link(GIT_COMMIT_URL.format(sha=sha), f"`{sha[:12]}`")


def _why_paragraph(
    result: TestRepairFixResult, culprit_author: str | None, addressed: bool
) -> list[str]:
    """Tell the author up front why they are on the email."""
    if not addressed:
        return []
    return [
        f"**{culprit_author}**, your {_commit_md(result.culprit_commit)} was backed"
        " out because it broke the tests below. The patch at the end is for your"
        " reland, not to land on its own.",
        "",
    ]


def _culprit_bullet(result: TestRepairFixResult, culprit_author: str | None) -> str:
    by = f" by {culprit_author}" if culprit_author else ""
    line = f"- **Culprit:** {_commit_md(result.culprit_commit)}{by}"
    if result.culprit_bug:
        line += ", " + md_link(
            BUG_URL.format(bug_id=result.culprit_bug), f"bug {result.culprit_bug}"
        )
    return line


def _reland_steps(
    bug_id: int | None, run_url: str, parent_revision: int | None
) -> list[str]:
    """How the author gets the pending revision into their own patch."""
    run_page = md_link(run_url, "run page")
    if parent_revision is None:
        bug = md_link(BUG_URL.format(bug_id=bug_id), f"bug {bug_id}")
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


def _fix_section(result: TestRepairFixResult) -> list[str]:
    """The fix when there is one, else the summary: they say the same thing."""
    if result.fix.strip():
        return ["", "## Fix", "", demote_headings(result.fix)]
    if result.summary.strip():
        return ["", result.summary.strip()]
    return []


def build_email(
    result: TestRepairFixResult,
    investigation: Investigation,
    *,
    task_id: str,
    run_id: str,
    analysis_run_id: str,
    culprit_author: str | None = None,
    classification: str | None = None,
    revision_pending: bool = False,
    parent_revision: int | None = None,
) -> tuple[str, str]:
    """The subject and markdown body of the patch email.

    ``revision_pending`` means the run recorded a ``phabricator.submit_patch``
    action that is waiting for approval, so the email says how to apply it;
    ``parent_revision`` is the culprit's revision it stacks on, when known.
    """
    verified = "verified" if result.verified else "unverified"
    subject = (
        f"[test-repair-fix] Patch for {groups_label(investigation)} regressed by"
        f" {result.culprit_commit[:12]}, {verified} ({investigation.project})"
    )
    run_url = RUN_URL.format(run_id=run_id)
    addressed = bool(recipients(result, culprit_author, classification))
    lines = [
        "# Proposed fix",
        "",
        *_why_paragraph(result, culprit_author, addressed),
        failing_bullet(investigation),
        jobs_bullet(investigation, task_id),
        push_bullet(investigation),
        _culprit_bullet(result, culprit_author),
        f"- **Analysis:** {RUN_URL.format(run_id=analysis_run_id)}",
        f"- **Hackbot Run:** {run_url}",
    ]
    lines += _fix_section(result)
    if revision_pending:
        lines += ["", *_reland_steps(result.culprit_bug, run_url, parent_revision)]
    if result.proposed_patch:
        # The diff itself is substituted for the placeholder when the mail is sent,
        # from the same artifact it attaches.
        lines += ["", "## Proposed patch", "", "```diff", PATCH_PLACEHOLDER, "```"]
    return subject, "\n".join(lines)
