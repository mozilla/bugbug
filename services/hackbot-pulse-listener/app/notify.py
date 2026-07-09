import base64
import logging
import re

from app import client
from app.config import settings
from app.models import RunContext

logger = logging.getLogger(__name__)

PATCH_ARTIFACT = "changes/changes.patch"
MAX_PATCH_LINES = 400


def send_email(ctx: RunContext, run_doc: dict) -> None:
    """Email the developer and team the build failure analysis.

    Only succeeded runs are notified.
    """
    if run_doc.get("status") != "succeeded":
        logger.info("Run %s did not succeed; skipping notification", ctx.run_id)
        return

    patch = _fetch_patch(ctx.run_id, run_doc)
    if settings.notify_only_with_patch and not patch:
        logger.info("Run %s produced no patch; skipping notification", ctx.run_id)
        return

    recipients = _recipients(ctx.developer_email)
    if not recipients:
        logger.info("No recipients for run %s; skipping notification", ctx.run_id)
        return
    if not (settings.sendgrid_api_key and settings.notification_sender):
        logger.info("SendGrid not configured; skipping email for run %s", ctx.run_id)
        return

    import markdown2
    import sendgrid
    from sendgrid.helpers.mail import (
        Attachment,
        Cc,
        Content,
        Disposition,
        FileContent,
        FileName,
        FileType,
        From,
        HtmlContent,
        Mail,
        Subject,
        To,
    )

    subject = (
        f"[build-repair] Build failure analysis for {ctx.repo}@{ctx.git_commit[:12]}"
    )

    body_md = _build_body(ctx, run_doc, patch)
    html = markdown2.markdown(body_md, extras=["fenced-code-blocks", "tables"])

    sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
    to_emails = [To(recipients[0])] + [Cc(addr) for addr in recipients[1:]]
    message = Mail(
        From(settings.notification_sender),
        to_emails,
        Subject(subject),
        Content("text/plain", body_md),
        HtmlContent(html),
    )
    if patch:
        message.attachment = Attachment(
            FileContent(base64.b64encode(patch.encode()).decode()),
            FileName("changes.patch"),
            FileType("text/x-patch"),
            Disposition("attachment"),
        )
    response = sg.send(message=message)
    logger.info(
        "Sent build-repair notification to %s (status %s)",
        ", ".join(recipients),
        response.status_code,
    )


def _recipients(developer_email: str | None) -> list[str]:
    """Recipients for a run: the revision author plus the team address, deduped.

    ``notification_override_email`` short-circuits to a single address so local
    testing never mails real developers or the team.
    """
    if settings.notification_override_email:
        return [settings.notification_override_email]
    recipients: list[str] = []
    for addr in (developer_email, settings.notification_team_email):
        if addr and addr not in recipients:
            recipients.append(addr)
    return recipients


def _fetch_patch(run_id: str, run_doc: dict) -> str | None:
    """Download the proposed-fix patch artifact, if the run produced one."""
    artifacts = run_doc.get("artifacts") or []
    if not any(a.get("name") == PATCH_ARTIFACT for a in artifacts):
        return None
    try:
        return client.get_artifact(run_id, PATCH_ARTIFACT)
    except Exception:
        logger.exception("Failed to fetch patch for run %s", run_id)
        return None


def _git_url(git_commit: str) -> str:
    return f"{settings.firefox_git_url.rstrip('/')}/commit/{git_commit}"


def _hg_url(hg_revision: str) -> str:
    return f"{settings.firefox_hg_url.rstrip('/')}/rev/{hg_revision}"


def _task_url(task_id: str) -> str:
    return f"{settings.taskcluster_root_url.rstrip('/')}/tasks/{task_id}"


def _bug_url(bug_id: object) -> str:
    return f"{settings.bugzilla_url.rstrip('/')}/show_bug.cgi?id={bug_id}"


def _build_body(ctx: RunContext, run_doc: dict, patch: str | None = None) -> str:
    summary = run_doc.get("summary") or {}
    findings = summary.get("findings") or {}

    lines = [
        "# Build failure analysis",
        "",
        f"- **Repository:** {ctx.repo}",
        f"- **Revision (git):** [`{ctx.git_commit[:12]}`]({_git_url(ctx.git_commit)})",
        f"- **Revision (hg):** [`{ctx.hg_revision[:12]}`]({_hg_url(ctx.hg_revision)})",
        f"- **Failed task:** [`{ctx.task_id}`]({_task_url(ctx.task_id)})",
    ]

    bug_id = findings.get("bug_id") or (run_doc.get("inputs") or {}).get("bug_id")
    if bug_id:
        lines.append(f"- **Bug:** [{bug_id}]({_bug_url(bug_id)})")

    if settings.hackbot_ui_url:
        run_url = f"{settings.hackbot_ui_url.rstrip('/')}/runs/{ctx.run_id}"
        lines.append(f"- **Run details:** {run_url}")

    if findings.get("summary"):
        lines += ["", "## Summary", "", _demote_headings(findings["summary"])]
    if findings.get("analysis"):
        lines += ["", "## Analysis", "", _demote_headings(findings["analysis"])]

    if findings.get("local_build_verified") is not None:
        lines += [
            "",
            "## Verification",
            "",
            f"- Local build verified: {findings['local_build_verified']}",
        ]

    if patch:
        lines += ["", "## Proposed patch", "", _patch_block(patch)]

    return "\n".join(lines)


def _demote_headings(md: str, by: int = 2) -> str:
    """Shift ATX headings down ``by`` levels so agent docs nest under our own.

    Lines inside code fences (and ``#include`` and the like, which lack the
    required space after ``#``) are left untouched.
    """
    out = []
    in_fence = False
    for line in md.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(line)
            continue
        match = re.match(r"(#{1,6}) ", line) if not in_fence else None
        if match:
            level = min(len(match.group(1)) + by, 6)
            line = "#" * level + line[len(match.group(1)) :]
        out.append(line)
    return "\n".join(out)


def _patch_block(patch: str) -> str:
    patch_lines = patch.splitlines()
    shown = patch_lines[:MAX_PATCH_LINES]
    block = ["```diff", *shown, "```"]
    if len(patch_lines) > MAX_PATCH_LINES:
        block.append(
            f"\n_Patch truncated to {MAX_PATCH_LINES} lines; "
            "see the attached changes.patch for the full diff._"
        )
    return "\n".join(block)
