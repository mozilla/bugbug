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


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing mail instead of hitting SendGrid."""
    calls = []

    def fake_send(sender, recipient, subject, body_md):
        calls.append((sender, recipient, subject, body_md))
        return 202

    monkeypatch.setattr(notifications, "_send_sync", fake_send)
    monkeypatch.setattr(settings, "sendgrid_api_key", "sg-test")
    monkeypatch.setattr(settings, "notification_sender", "hackbot@mozilla.com")
    monkeypatch.setattr(settings, "notification_override_email", "")
    return calls


def test_build_message_links_to_run_page(monkeypatch):
    monkeypatch.setattr(settings, "ui_base_url", "https://ui.example/")
    run = _FakeRun(status=RunStatus.timed_out.value)
    subject, body = build_message(run)
    assert subject == "[Hackbot] bug-fix run timed out"
    assert f"https://ui.example/runs/{run.run_id}" in body


async def test_sends_to_requester(sent):
    run = _FakeRun()
    assert await notify_requester(run) is True
    assert len(sent) == 1
    sender, recipient, subject, _ = sent[0]
    assert sender == "hackbot@mozilla.com"
    assert recipient == "someone@mozilla.com"
    assert subject == "[Hackbot] bug-fix run succeeded"


async def test_skips_runs_without_requester(sent):
    assert await notify_requester(_FakeRun(requested_by=None)) is False
    assert sent == []


async def test_override_email_replaces_recipient(sent, monkeypatch):
    monkeypatch.setattr(settings, "notification_override_email", "dev@example.com")
    assert await notify_requester(_FakeRun()) is True
    assert sent[0][1] == "dev@example.com"


async def test_unconfigured_sendgrid_is_a_quiet_noop(sent, monkeypatch, caplog):
    monkeypatch.setattr(settings, "sendgrid_api_key", "")
    assert await notify_requester(_FakeRun()) is False
    assert sent == []
    assert "not configured" in caplog.text


async def test_send_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(settings, "sendgrid_api_key", "sg-test")
    monkeypatch.setattr(settings, "notification_sender", "hackbot@mozilla.com")

    def boom(*_a):
        raise RuntimeError("sendgrid down")

    monkeypatch.setattr(notifications, "_send_sync", boom)
    assert await notify_requester(_FakeRun()) is False
    assert "Failed to notify" in caplog.text
