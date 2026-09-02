# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The email a finished run sends about the build failure.

Recorded as an ``email.send`` action rather than sent from the run, so it is
visible in the hackbot UI before it lands and is delivered at most once (see
``hackbot_runtime.actions.email``).

It reaches the developer who pushed the failing change and the author the agent
blamed; the team address is added apply-side. Every identifier a recipient would
otherwise have to look up -- revisions, task, bug, run -- is a link.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hackbot_runtime.actions.email import PATCH_PLACEHOLDER, demote_headings
from hackbot_runtime.actions.slack import HACKBOT_UI_URL

from .agent import BuildRepairResult
from .resolve import PushInfo

GIT_COMMIT_URL = "https://github.com/mozilla-firefox/firefox/commit/{sha}"
HG_REV_URL = "https://hg.mozilla.org/mozilla-unified/rev/{rev}"
TASK_URL = "https://firefox-ci-tc.services.mozilla.com/tasks/{task_id}"
TREEHERDER_JOB_URL = (
    "https://treeherder.mozilla.org/#/jobs"
    "?repo={project}&revision={revision}&selectedTaskRun={task_id}"
)
BUG_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}"
RUN_URL = HACKBOT_UI_URL.rstrip("/") + "/runs/{run_id}"


def resolve_author_email(source_repo: Path, sha: str | None) -> str | None:
    """The blamed commit's author email, so the notification reaches them."""
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


def _link(url: str, label: str) -> str:
    return f"[{label}]({url})"


def recipients(push: PushInfo, blamed_author: str | None) -> list[str]:
    """Who the failure concerns: the blamed author first, then the pusher."""
    return [address for address in (blamed_author, push.developer_email) if address]


def _why_section(
    push: PushInfo, blamed_commit: str | None, author: str | None
) -> list[str]:
    """Explain why each recipient is on the email."""
    notes = []
    if push.developer_email:
        notes.append(
            f"- **{push.developer_email}** pushed the change whose build failed."
        )
    if blamed_commit:
        who = f"**{author}** authored" if author else "The agent believes"
        link = _link(
            GIT_COMMIT_URL.format(sha=blamed_commit), f"`{blamed_commit[:12]}`"
        )
        notes.append(f"- {who} {link}, which introduced the failure.")
    return ["", "## Why you're receiving this", "", *notes] if notes else []


def _analysis_sections(result: BuildRepairResult) -> list[str]:
    lines: list[str] = []
    for text, title in ((result.summary, "Summary"), (result.analysis, "Analysis")):
        if text:
            lines += ["", f"## {title}", "", demote_headings(text)]
    return lines


def build_email(
    result: BuildRepairResult,
    push: PushInfo,
    *,
    task_id: str,
    run_id: str,
    has_patch: bool = False,
    blamed_author: str | None = None,
) -> tuple[str, str]:
    """The subject and markdown body of the build-failure email."""
    failure_commit = push.git_commits[0]
    subject = (
        f"[build-repair] Build failure analysis for "
        f"{push.project}@{failure_commit[:12]}"
    )

    lines = [
        "# Build failure analysis",
        "",
        f"- **Repository:** {push.project}",
        "- **Revision (git):** "
        + _link(GIT_COMMIT_URL.format(sha=failure_commit), f"`{failure_commit[:12]}`"),
        "- **Failed task:** " + _link(TASK_URL.format(task_id=task_id), f"`{task_id}`"),
    ]
    # Absent only when the run was pinned to a git commit by hand; both lines are
    # keyed on the hg revision, so they go together.
    if push.hg_revision:
        lines += [
            "- **Revision (hg):** "
            + _link(
                HG_REV_URL.format(rev=push.hg_revision), f"`{push.hg_revision[:12]}`"
            ),
            "- **Treeherder:** "
            + _link(
                TREEHERDER_JOB_URL.format(
                    project=push.project, revision=push.hg_revision, task_id=task_id
                ),
                "jobs",
            ),
        ]

    if result.blamed_commit:
        by = f" by {blamed_author}" if blamed_author else ""
        lines.append(
            "- **Likely culprit:** "
            + _link(
                GIT_COMMIT_URL.format(sha=result.blamed_commit),
                f"`{result.blamed_commit[:12]}`",
            )
            + by
        )
    else:
        lines.append(
            "- **Not caused by this push:** the failure is pre-existing or "
            "infrastructure, so no commit here is blamed."
        )
    if result.bug_id:
        lines.append(
            "- **Bug:** "
            + _link(BUG_URL.format(bug_id=result.bug_id), str(result.bug_id))
        )
    lines.append("- **Run details:** " + RUN_URL.format(run_id=run_id))

    lines += _why_section(push, result.blamed_commit, blamed_author)
    lines += _analysis_sections(result)

    if result.local_build_verified is not None:
        lines += [
            "",
            "## Verification",
            "",
            f"- Local build verified: {result.local_build_verified}",
        ]
    if has_patch:
        # The diff itself is substituted for the placeholder when the mail is sent,
        # from the same artifact it attaches.
        lines += ["", "## Proposed patch", "", "```diff", PATCH_PLACEHOLDER, "```"]
    return subject, "\n".join(lines)
