import logging

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(
    developer_email: str | None,
    revision: str,
    repo: str,
    run_id: str,
    run_doc: dict,
) -> None:
    """Email the developer the run outcome. No-op (logged) if not configured."""
    recipient = settings.notification_override_email or developer_email
    if not recipient:
        logger.info("No developer email for run %s; skipping notification", run_id)
        return
    if not (settings.sendgrid_api_key and settings.notification_sender):
        logger.info("SendGrid not configured; skipping email for run %s", run_id)
        return

    import sendgrid
    from sendgrid.helpers.mail import Content, From, Mail, Subject, To

    status = run_doc.get("status", "unknown")
    subject = f"[build-repair] {status} for {repo}@{revision[:12]}"

    sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
    message = Mail(
        From(settings.notification_sender),
        To(recipient),
        Subject(subject),
        Content("text/plain", _build_body(revision, repo, run_id, run_doc)),
    )
    response = sg.send(message=message)
    logger.info(
        "Sent build-repair notification to %s (status %s)",
        recipient,
        response.status_code,
    )


def _build_body(revision: str, repo: str, run_id: str, run_doc: dict) -> str:
    status = run_doc.get("status", "unknown")
    lines = [
        f"The build-repair agent finished with status: {status}.",
        "",
        f"Repository: {repo}",
        f"Revision:   {revision}",
    ]

    if settings.hackbot_ui_url:
        lines += [
            "",
            f"Run details: {settings.hackbot_ui_url.rstrip('/')}/runs/{run_id}",
        ]

    summary = run_doc.get("summary") or {}
    findings = summary.get("findings") or {}
    if findings.get("summary"):
        lines += ["", "Summary:", findings["summary"]]
    if findings.get("analysis"):
        lines += ["", "Analysis:", findings["analysis"]]
    if findings.get("local_build_verified") is not None:
        lines += ["", f"Local build verified: {findings['local_build_verified']}"]
    if findings.get("treeherder_url"):
        lines += [f"Try push: {findings['treeherder_url']}"]

    error = run_doc.get("error") or summary.get("error")
    if status != "succeeded" and error:
        lines += ["", f"Error: {error}"]

    return "\n".join(lines)
