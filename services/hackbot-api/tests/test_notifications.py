"""Tests for the run-completion email to the requester (app/notifications.py)."""

import uuid
from dataclasses import dataclass, field

import pytest
from app import notifications
from app.config import settings
from app.notifications import build_message, notify_requester
from app.schemas import RunStatus


@dataclass
class _FakeRun:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent: str = "bug-fix"
    status: str = RunStatus.succeeded.value
    requested_by: str | None = "someone@mozilla.com"
    inputs: dict = field(default_factory=dict)


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing mail instead of hitting SendGrid."""
    calls = []

    def fake_send(recipient, subject, html_body):
        calls.append((recipient, subject, html_body))
        return 202

    monkeypatch.setattr(notifications, "_send_sync", fake_send)
    monkeypatch.setattr(settings, "sendgrid_api_key", "sg-test")
    monkeypatch.setattr(settings, "notification_sender", "hackbot@mozilla.com")
    monkeypatch.setattr(settings, "notification_override_email", "")
    return calls


def test_build_message_links_to_run_page(monkeypatch):
    monkeypatch.setattr(settings, "ui_base_url", "https://ui.example/")
    run = _FakeRun(status=RunStatus.timed_out.value)
    subject, html_body = build_message(run)
    url = f"https://ui.example/runs/{run.run_id}"
    assert subject == f"[Hackbot] bug-fix run {str(run.run_id)[:8]} timed out"
    assert f"bug-fix run {str(run.run_id)[:8]}" in html_body
    assert f'<a href="{url}">' in html_body
    assert "<strong>timed out</strong>" in html_body


async def test_sends_to_requester(sent):
    run = _FakeRun()
    assert await notify_requester(run) is True
    assert len(sent) == 1
    recipient, subject, _ = sent[0]
    assert recipient == "someone@mozilla.com"
    assert subject == f"[Hackbot] bug-fix run {str(run.run_id)[:8]} succeeded"


def test_build_message_includes_bug_id():
    run = _FakeRun(
        run_id=uuid.UUID("ab603010-c278-4d55-bc29-f89463f78906"),
        inputs={"bug_id": 123456},
    )
    subject, html_body = build_message(run)
    assert subject == "[Hackbot] bug-fix run ab603010 for bug 123456 succeeded"
    assert "bug-fix run ab603010 for bug 123456" in html_body


async def test_override_email_replaces_recipient(sent, monkeypatch):
    monkeypatch.setattr(settings, "notification_override_email", "dev@example.com")
    assert await notify_requester(_FakeRun()) is True
    assert sent[0][0] == "dev@example.com"


async def test_send_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(settings, "sendgrid_api_key", "sg-test")
    monkeypatch.setattr(settings, "notification_sender", "hackbot@mozilla.com")

    def boom(*_a):
        raise RuntimeError("sendgrid down")

    monkeypatch.setattr(notifications, "_send_sync", boom)
    assert await notify_requester(_FakeRun()) is False
    assert "Failed to notify" in caplog.text
