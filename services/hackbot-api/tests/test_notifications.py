"""Tests for the run-completion email to the requester (app/notifications.py)."""

import uuid
from dataclasses import dataclass, field

import pytest
from app import notifications
from app.notifications import build_message, notify_requester, run_label
from app.schemas import RunStatus


@dataclass
class _FakeRun:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent: str = "bug-fix"
    status: str = RunStatus.succeeded.value
    inputs: dict = field(default_factory=lambda: {"bug_id": 1234567})
    requested_by: str | None = "someone@mozilla.com"
    error: str | None = None


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing mail instead of hitting SendGrid."""
    calls = []

    def fake_send(sender, recipient, subject, body_md):
        calls.append((sender, recipient, subject, body_md))
        return 202

    monkeypatch.setattr(notifications, "_send_sync", fake_send)
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test")
    monkeypatch.setenv("NOTIFICATION_SENDER", "hackbot@mozilla.com")
    monkeypatch.delenv("NOTIFICATION_OVERRIDE_EMAIL", raising=False)
    return calls


def test_run_label_mirrors_ui():
    assert run_label({"bug_id": 42}) == "bug 42"
    assert run_label({"git_commit": "abcdef0123456789"}) == "commit abcdef012345"
    assert run_label({"feature_name": " Tab groups "}) == "Tab groups"
    assert run_label({}) == ""


def test_build_message_links_to_run_page(monkeypatch):
    monkeypatch.setattr(notifications.settings, "ui_base_url", "https://ui.example/")
    run = _FakeRun(status=RunStatus.timed_out.value)
    subject, body = build_message(run)
    assert subject == "[Hackbot] bug-fix on bug 1234567 timed out"
    assert f"https://ui.example/runs/{run.run_id}" in body
    assert "```" not in body


def test_build_message_includes_error():
    run = _FakeRun(status=RunStatus.failed.value, error="boom")
    _, body = build_message(run)
    assert "failed" in body
    assert "boom" in body


async def test_sends_to_requester(sent):
    run = _FakeRun()
    assert await notify_requester(run) is True
    assert len(sent) == 1
    sender, recipient, subject, _ = sent[0]
    assert sender == "hackbot@mozilla.com"
    assert recipient == "someone@mozilla.com"
    assert subject.startswith("[Hackbot] bug-fix on bug 1234567")


async def test_skips_runs_without_requester(sent):
    assert await notify_requester(_FakeRun(requested_by=None)) is False
    assert sent == []


async def test_override_email_replaces_recipient(sent, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_OVERRIDE_EMAIL", "dev@example.com")
    assert await notify_requester(_FakeRun()) is True
    assert sent[0][1] == "dev@example.com"


async def test_unconfigured_sendgrid_is_a_quiet_noop(sent, monkeypatch, caplog):
    monkeypatch.delenv("SENDGRID_API_KEY")
    assert await notify_requester(_FakeRun()) is False
    assert sent == []
    assert "not configured" in caplog.text


async def test_send_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test")
    monkeypatch.setenv("NOTIFICATION_SENDER", "hackbot@mozilla.com")

    def boom(*_a):
        raise RuntimeError("sendgrid down")

    monkeypatch.setattr(notifications, "_send_sync", boom)
    assert await notify_requester(_FakeRun()) is False
    assert "Failed to notify" in caplog.text
