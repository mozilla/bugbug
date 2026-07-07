from unittest.mock import MagicMock, patch

from app import notify


def test_skips_without_developer_email():
    # Must not raise even with no SendGrid config.
    notify.send_email(None, "rev", "try", "run-1", {"status": "succeeded"})


def test_skips_without_sendgrid_config(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", None)
    monkeypatch.setattr(notify.settings, "notification_sender", None)
    notify.send_email("dev@mozilla.com", "rev", "try", "run-1", {"status": "succeeded"})


def test_body_contains_ui_link_and_summary(monkeypatch):
    monkeypatch.setattr(notify.settings, "hackbot_ui_url", "https://ui.example/")
    body = notify._build_body(
        "deadbeefcafe1234",
        "try",
        "run-1",
        {
            "status": "succeeded",
            "summary": {
                "findings": {
                    "summary": "Fixed a missing include",
                    "analysis": "The commit removed a needed header",
                    "local_build_verified": True,
                }
            },
        },
    )
    assert "https://ui.example/runs/run-1" in body
    assert "Fixed a missing include" in body
    assert "The commit removed a needed header" in body
    assert "Local build verified: True" in body


def test_failure_body_includes_error():
    body = notify._build_body(
        "deadbeef", "try", "run-1", {"status": "failed", "error": "build still broken"}
    )
    assert "build still broken" in body


def test_sends_email_when_configured(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")

    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with patch("sendgrid.SendGridAPIClient", return_value=fake_client):
        notify.send_email(
            "dev@mozilla.com",
            "rev",
            "try",
            "run-1",
            {"status": "succeeded", "summary": {}},
        )

    fake_client.send.assert_called_once()


def test_override_sends_even_without_developer_email(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(
        notify.settings, "notification_override_email", "me@mozilla.com"
    )

    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with patch("sendgrid.SendGridAPIClient", return_value=fake_client):
        notify.send_email(
            None, "rev", "try", "run-1", {"status": "succeeded", "summary": {}}
        )
