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


def settings_test_repair_address() -> str | None:
    return notify.settings.test_repair_notification_email


def _test_repair_ctx(**over):
    over.setdefault("test_groups", ["dom/base/test/mochitest.ini"])
    return _ctx(agent="test-repair", **over)


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


def test_body_contains_treeherder_link():
    body = notify._build_body(_ctx(), {"status": "succeeded", "summary": {}})
    assert (
        "https://treeherder.mozilla.org/#/jobs?repo=autoland"
        "&revision=0123456789ab&selectedTaskRun=TASK123" in body
    )


def test_body_contains_culprit_when_blamed():
    run_doc = {
        "status": "succeeded",
        "summary": {"findings": {"blamed_commit": "abcdef123456789"}},
    }
    body = notify._build_body(_ctx(), run_doc, blamed_author="culprit@mozilla.com")
    assert "Likely culprit" in body
    assert "https://github.com/mozilla-firefox/firefox/commit/abcdef123456789" in body
    assert "by culprit@mozilla.com" in body


def test_body_omits_culprit_when_absent():
    body = notify._build_body(_ctx(), {"status": "succeeded", "summary": {}})
    assert "Likely culprit" not in body


def test_recipients_blamed_author_is_primary(monkeypatch):
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(notify.settings, "notification_team_email", "team@mozilla.com")
    assert notify._recipients("culprit@mozilla.com", "pusher@mozilla.com") == [
        "culprit@mozilla.com",
        "pusher@mozilla.com",
        "team@mozilla.com",
    ]


def test_email_goes_to_blamed_author_first(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(notify.settings, "notification_team_email", None)
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", False)

    run_doc = {
        "status": "succeeded",
        "summary": {"findings": {"blamed_commit": "cafe1234"}},
    }
    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with (
        patch("sendgrid.SendGridAPIClient", return_value=fake_client),
        patch.object(
            notify.github, "commit_author_email", return_value="culprit@mozilla.com"
        ) as author,
    ):
        notify.send_email(
            _ctx(developer_email="pusher@mozilla.com"),
            run_doc,
        )

    author.assert_called_once_with("cafe1234")

    personalizations = fake_client.send.call_args.kwargs["message"].get()[
        "personalizations"
    ][0]
    assert personalizations["to"] == [{"email": "culprit@mozilla.com"}]
    assert personalizations["cc"] == [{"email": "pusher@mozilla.com"}]


def test_email_sets_team_reply_to(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", False)
    monkeypatch.setattr(
        notify.settings, "notification_team_email", "hackbot-developers@mozilla.com"
    )

    run_doc = {"status": "succeeded", "summary": {"findings": {}}}
    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with (
        patch("sendgrid.SendGridAPIClient", return_value=fake_client),
        patch.object(notify.github, "commit_author_email", return_value=None),
    ):
        notify.send_email(_ctx(), run_doc)

    message = fake_client.send.call_args.kwargs["message"].get()
    assert message["reply_to"] == {"email": "hackbot-developers@mozilla.com"}
    body = message["content"][0]["value"]
    assert "reaches the hackbot team" in body


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
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", False)

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
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", False)

    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with patch("sendgrid.SendGridAPIClient", return_value=fake_client):
        notify.send_email(
            _ctx(developer_email=None), {"status": "succeeded", "summary": {}}
        )

    fake_client.send.assert_called_once()


def test_skips_when_no_patch_and_notify_only_with_patch(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", True)

    with patch("sendgrid.SendGridAPIClient") as sg:
        notify.send_email(_ctx(), {"status": "succeeded", "summary": {}})
    sg.assert_not_called()


def test_sends_without_patch_when_gate_disabled(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", False)

    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with patch("sendgrid.SendGridAPIClient", return_value=fake_client):
        notify.send_email(_ctx(), {"status": "succeeded", "summary": {}})

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


def _test_repair_findings(**over):
    base = {
        "classification": "regression",
        "recommendation": "backout",
        "culprit_commit": "abc123def456",
        "confidence": 0.8,
        "last_green_revision": "green99",
        "summary": "A landed commit removed a null check.",
        "analysis": "# Root cause\nThe diff dropped validation.",
    }
    base.update(over)
    return base


def test_test_repair_body_leads_with_recommendation():
    body = notify._build_test_repair_body(
        _test_repair_ctx(), _test_repair_findings(), None, "culprit@mozilla.com"
    )
    assert "Test failure analysis" in body
    assert "BACK OUT the culprit" in body
    assert "dom/base/test/mochitest.ini" in body
    assert "abc123def456"[:12] in body
    assert "by culprit@mozilla.com" in body
    assert "green99" in body
    assert "## Analysis" in body


def test_test_repair_body_names_every_failing_group():
    ctx = _test_repair_ctx(
        test_groups=["dom/base/test/mochitest.ini", "layout/test/mochitest.ini"]
    )
    body = notify._build_test_repair_body(ctx, _test_repair_findings(), None, None)
    assert "dom/base/test/mochitest.ini" in body
    assert "layout/test/mochitest.ini" in body


def test_test_repair_subject_summarizes_multiple_groups():
    ctx = _test_repair_ctx(test_groups=["a/mochitest.ini", "b/mochitest.ini"])
    assert notify._test_groups_label(ctx) == "a/mochitest.ini (+1 more)"
    assert (
        notify._test_groups_label(_test_repair_ctx()) == "dom/base/test/mochitest.ini"
    )
    assert notify._test_groups_label(_test_repair_ctx(test_groups=[])) == "task TASK123"


def test_test_repair_body_omits_unmapped_git_revision():
    # An unmapped revision must not render an empty commit link.
    body = notify._build_test_repair_body(
        _test_repair_ctx(git_commit=""), _test_repair_findings(), None, None
    )
    assert "Revision (git)" not in body
    assert "firefox/commit/)" not in body
    assert "Revision (hg)" in body


def test_test_repair_intermittent_body_says_do_not_backout():
    findings = _test_repair_findings(
        classification="intermittent",
        recommendation="do_not_backout",
        culprit_commit=None,
    )
    body = notify._build_test_repair_body(_test_repair_ctx(), findings, None, None)
    assert "DO NOT back out" in body
    assert "Culprit commit" not in body


def test_test_repair_recipients_address_then_culprit(monkeypatch):
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(
        notify.settings, "test_repair_notification_email", "test-repair@mozilla.com"
    )
    monkeypatch.setattr(notify.settings, "notification_team_email", "team@mozilla.com")
    assert notify._recipients(
        settings_test_repair_address(), "culprit@mozilla.com"
    ) == [
        "test-repair@mozilla.com",
        "culprit@mozilla.com",
        "team@mozilla.com",
    ]


def test_test_repair_recipients_override_wins(monkeypatch):
    monkeypatch.setattr(
        notify.settings, "notification_override_email", "me@mozilla.com"
    )
    monkeypatch.setattr(
        notify.settings, "test_repair_notification_email", "test-repair@mozilla.com"
    )
    assert notify._recipients(
        settings_test_repair_address(), "culprit@mozilla.com"
    ) == ["me@mozilla.com"]


def test_test_repair_intermittent_sends_without_patch(monkeypatch):
    # No patch, notify_only_with_patch True -> test-repair still sends (unlike build-repair).
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(notify.settings, "notify_only_with_patch", True)
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(
        notify.settings, "test_repair_notification_email", "test-repair@mozilla.com"
    )
    monkeypatch.setattr(notify.settings, "notification_team_email", None)

    run_doc = {
        "status": "succeeded",
        "summary": {
            "findings": _test_repair_findings(
                classification="intermittent",
                recommendation="do_not_backout",
                culprit_commit=None,
            )
        },
    }
    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with patch("sendgrid.SendGridAPIClient", return_value=fake_client):
        notify.send_email(_test_repair_ctx(), run_doc)

    fake_client.send.assert_called_once()
    personalizations = fake_client.send.call_args.kwargs["message"].get()[
        "personalizations"
    ][0]
    assert personalizations["to"] == [{"email": "test-repair@mozilla.com"}]


def test_test_repair_regression_ccs_culprit_author(monkeypatch):
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "from@mozilla.com")
    monkeypatch.setattr(notify.settings, "notification_override_email", None)
    monkeypatch.setattr(
        notify.settings, "test_repair_notification_email", "test-repair@mozilla.com"
    )
    monkeypatch.setattr(notify.settings, "notification_team_email", None)

    run_doc = {"status": "succeeded", "summary": {"findings": _test_repair_findings()}}
    fake_client = MagicMock()
    fake_client.send.return_value = MagicMock(status_code=202)
    with (
        patch("sendgrid.SendGridAPIClient", return_value=fake_client),
        patch.object(
            notify.github, "commit_author_email", return_value="culprit@mozilla.com"
        ) as author,
    ):
        notify.send_email(_test_repair_ctx(), run_doc)

    author.assert_called_once_with("abc123def456")
    personalizations = fake_client.send.call_args.kwargs["message"].get()[
        "personalizations"
    ][0]
    assert personalizations["to"] == [{"email": "test-repair@mozilla.com"}]
    assert personalizations["cc"] == [{"email": "culprit@mozilla.com"}]


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
