"""Tests for the email-notification module."""

import uuid
from unittest.mock import patch

import pytest
from app import notifications
from app.config import settings
from app.database.models import Run


def make_run(**overrides) -> Run:
    run = Run(
        run_id=uuid.uuid4(),
        agent="bug-fix",
        status="succeeded",
        inputs={},
        results_prefix="runs/x/",
        artifacts=[],
    )
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


@pytest.mark.parametrize(
    "value,expected",
    [
        ("dev@example.com", True),
        ("dev+tag@example.co.uk", True),
        ("not-an-email", False),
        ("missing-domain@", False),
        ("@missing-local.com", False),
        ("has space@example.com", False),
        ("", False),
    ],
)
def test_is_valid_email(value, expected):
    assert notifications.is_valid_email(value) is expected


def test_build_run_url_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "hackbot_ui_base_url", "")
    assert notifications.build_run_url(uuid.uuid4()) is None


def test_build_run_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "hackbot_ui_base_url", "https://hackbot.example/")
    run_id = uuid.uuid4()
    assert (
        notifications.build_run_url(run_id) == f"https://hackbot.example/runs/{run_id}"
    )


def test_build_lines_includes_error_and_run_url(monkeypatch):
    monkeypatch.setattr(settings, "hackbot_ui_base_url", "https://hackbot.example")
    run = make_run(notify_email="dev@example.com", status="failed", error="boom")

    lines = notifications._build_lines(run)

    assert "Status: failed" in lines
    assert "Error: boom" in lines
    assert f"Results: https://hackbot.example/runs/{run.run_id}" in lines


def test_build_lines_omits_error_and_url_when_absent(monkeypatch):
    monkeypatch.setattr(settings, "hackbot_ui_base_url", "")
    run = make_run(notify_email="dev@example.com", status="succeeded", error=None)

    lines = notifications._build_lines(run)

    assert not any(line.startswith("Error:") for line in lines)
    assert not any(line.startswith("Results:") for line in lines)


def test_build_message_smoke(monkeypatch):
    monkeypatch.setattr(settings, "notification_sender_email", "hackbot@example.com")
    run = make_run(notify_email="dev@example.com")

    # Must not raise -- exercises the sendgrid Mail() construction path.
    message = notifications._build_message(run)

    assert message is not None


async def test_notify_run_complete_noop_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "")
    run = make_run(notify_email="dev@example.com")

    with patch.object(notifications, "_send_sync") as send:
        await notifications.notify_run_complete(run)

    send.assert_not_called()


async def test_notify_run_complete_noop_without_notify_email(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.fake")
    monkeypatch.setattr(settings, "notification_sender_email", "hackbot@example.com")
    run = make_run(notify_email=None)

    with patch.object(notifications, "_send_sync") as send:
        await notifications.notify_run_complete(run)

    send.assert_not_called()


async def test_notify_run_complete_noop_without_sender(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.fake")
    monkeypatch.setattr(settings, "notification_sender_email", "")
    run = make_run(notify_email="dev@example.com")

    with patch.object(notifications, "_send_sync") as send:
        await notifications.notify_run_complete(run)

    send.assert_not_called()


async def test_notify_run_complete_sends_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.fake")
    monkeypatch.setattr(settings, "notification_sender_email", "hackbot@example.com")
    run = make_run(notify_email="dev@example.com", status="failed", error="boom")

    with patch.object(notifications, "_send_sync") as send:
        await notifications.notify_run_complete(run)

    send.assert_called_once_with(run)


async def test_notify_run_complete_swallows_send_errors(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.fake")
    monkeypatch.setattr(settings, "notification_sender_email", "hackbot@example.com")
    run = make_run(notify_email="dev@example.com")

    with patch.object(notifications, "_send_sync", side_effect=RuntimeError("boom")):
        # Must not raise -- a broken notification must never break reconciliation.
        await notifications.notify_run_complete(run)
