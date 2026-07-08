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


def test_body_includes_patch():
    body = notify._build_body(
        "deadbeef",
        "autoland",
        "run-1",
        {"status": "succeeded", "summary": {}},
        patch="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n",
    )
    assert "## Proposed patch" in body
    assert "```diff" in body
    assert "+new" in body


def test_fetch_patch_returns_none_without_artifact():
    assert notify._fetch_patch("run-1", {"artifacts": []}) is None


def test_fetch_patch_downloads_listed_artifact():
    run_doc = {"artifacts": [{"name": notify.PATCH_ARTIFACT}]}
    with patch.object(notify.client, "get_artifact", return_value="THE PATCH") as ga:
        assert notify._fetch_patch("run-1", run_doc) == "THE PATCH"
    ga.assert_called_once_with("run-1", notify.PATCH_ARTIFACT)


def test_skips_when_not_succeeded(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    with patch("sendgrid.SendGridAPIClient") as sg:
        notify.send_email(
            "dev@mozilla.com", "rev", "autoland", "run-1", {"status": "failed"}
        )
    sg.assert_not_called()


def test_analysis_headings_demoted_under_section():
    run_doc = {
        "status": "succeeded",
        "summary": {"findings": {"analysis": "# Root cause\n\n## Details\ntext"}},
    }
    body = notify._build_body("rev", "autoland", "run-1", run_doc)
    assert "## Analysis" in body
    assert "### Root cause" in body
    assert "#### Details" in body


def test_demote_headings_leaves_code_fences_and_includes_alone():
    md = "```cpp\n#include <foo>\n```"
    assert notify._demote_headings(md) == md


def test_recipients_author_and_team(monkeypatch):
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(notify.settings, "notification_team_email", "team@mozilla.com")
    assert notify._recipients("dev@mozilla.com") == [
        "dev@mozilla.com",
        "team@mozilla.com",
    ]


def test_recipients_override_wins(monkeypatch):
    monkeypatch.setattr(
        notify.settings, "notification_override_email", "me@mozilla.com"
    )
    monkeypatch.setattr(notify.settings, "notification_team_email", "team@mozilla.com")
    assert notify._recipients("dev@mozilla.com") == ["me@mozilla.com"]


def test_recipients_dedupes_and_skips_empty(monkeypatch):
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(notify.settings, "notification_team_email", "dev@mozilla.com")
    assert notify._recipients("dev@mozilla.com") == ["dev@mozilla.com"]
    monkeypatch.setattr(notify.settings, "notification_team_email", None)
    assert notify._recipients(None) == []
