import base64
from unittest.mock import MagicMock, patch

from app import notify
from app.models import RunContext


def _ctx(**over):
    base = dict(
        run_id="run-1",
        repo="autoland",
        git_commit="deadbeefcafe",
        hg_revision="0123456789ab",
        task_id="TASK123",
        developer_email="dev@mozilla.com",
    )
    base.update(over)
    return RunContext(**base)


def test_skips_without_recipient():
    # No developer, no team, no override -> nothing to send, must not raise.
    notify.send_email(_ctx(developer_email=None), {"status": "succeeded"})


def test_skips_without_sendgrid_config(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", None)
    monkeypatch.setattr(notify.settings, "notification_sender", None)
    notify.send_email(_ctx(), {"status": "succeeded"})


def test_skips_when_not_succeeded(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    with patch("sendgrid.SendGridAPIClient") as sg:
        notify.send_email(_ctx(), {"status": "failed"})
    sg.assert_not_called()


def test_body_contains_source_links():
    body = notify._build_body(_ctx(), {"status": "succeeded", "summary": {}})
    assert "https://github.com/mozilla-firefox/firefox/commit/deadbeefcafe" in body
    assert "https://hg.mozilla.org/mozilla-unified/rev/0123456789ab" in body
    assert "https://firefox-ci-tc.services.mozilla.com/tasks/TASK123" in body


def test_body_contains_bug_link_when_present():
    run_doc = {"status": "succeeded", "summary": {"findings": {"bug_id": 12345}}}
    body = notify._build_body(_ctx(), run_doc)
    assert "https://bugzilla.mozilla.org/show_bug.cgi?id=12345" in body

    no_bug = notify._build_body(_ctx(), {"status": "succeeded", "summary": {}})
    assert "show_bug.cgi" not in no_bug


def test_body_contains_ui_link_and_summary(monkeypatch):
    monkeypatch.setattr(notify.settings, "hackbot_ui_url", "https://ui.example/")
    body = notify._build_body(
        _ctx(),
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


def test_body_includes_patch():
    body = notify._build_body(
        _ctx(),
        {"status": "succeeded", "summary": {}},
        patch="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n",
    )
    assert "## Proposed patch" in body
    assert "```diff" in body
    assert "+new" in body


def test_analysis_headings_demoted_under_section():
    run_doc = {
        "status": "succeeded",
        "summary": {"findings": {"analysis": "# Root cause\n\n## Details\ntext"}},
    }
    body = notify._build_body(_ctx(), run_doc)
    assert "## Analysis" in body
    assert "### Root cause" in body
    assert "#### Details" in body


def test_demote_headings_leaves_code_fences_and_includes_alone():
    md = "```cpp\n#include <foo>\n```"
    assert notify._demote_headings(md) == md


def test_sends_email_when_configured(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")

    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with patch("sendgrid.SendGridAPIClient", return_value=fake_client):
        notify.send_email(_ctx(), {"status": "succeeded", "summary": {}})

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
            _ctx(developer_email=None), {"status": "succeeded", "summary": {}}
        )

    fake_client.send.assert_called_once()


def test_fetch_patch_returns_none_without_artifact():
    assert notify._fetch_patch("run-1", {"artifacts": []}) is None


def test_fetch_patch_downloads_listed_artifact():
    run_doc = {"artifacts": [{"name": notify.PATCH_ARTIFACT}]}
    with patch.object(notify.client, "get_artifact", return_value="THE PATCH") as ga:
        assert notify._fetch_patch("run-1", run_doc) == "THE PATCH"
    ga.assert_called_once_with("run-1", notify.PATCH_ARTIFACT)


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


def test_attaches_patch_file(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    run_doc = {
        "status": "succeeded",
        "artifacts": [{"name": notify.PATCH_ARTIFACT}],
        "summary": {"findings": {}},
    }
    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with (
        patch("sendgrid.SendGridAPIClient", return_value=fake_client),
        patch.object(notify.client, "get_artifact", return_value="DIFF-CONTENT"),
    ):
        notify.send_email(_ctx(), run_doc)

    attachments = fake_client.send.call_args.kwargs["message"].get()["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "changes.patch"
    assert base64.b64decode(attachments[0]["content"]).decode() == "DIFF-CONTENT"
