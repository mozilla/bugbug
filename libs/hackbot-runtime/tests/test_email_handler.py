"""Tests for the apply-side email handler.

Mocks SendGrid so these exercise the handler's own logic -- recipient policy,
attachments, error handling -- without touching a network.
"""

import base64
import json

import pytest
from hackbot_runtime.actions.handlers import email_handler


def _ctx(artifacts=None):
    async def download(key):
        if artifacts is None or key not in artifacts:
            raise FileNotFoundError(key)
        return artifacts[key]

    from hackbot_runtime.actions.handlers import ApplyContext

    return ApplyContext(
        run_id="run-1", agent="build-repair", download_artifact=download
    )


class _FakeClient:
    sent = None

    def __init__(self, api_key):
        self.api_key = api_key

    def send(self, message):
        _FakeClient.sent = message
        return type("Response", (), {"status_code": 202})()


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    _FakeClient.sent = None
    monkeypatch.setenv("SENDGRID_API_KEY", "key")
    monkeypatch.setenv("NOTIFICATION_SENDER", "hackbot@mozilla.com")
    monkeypatch.setenv("NOTIFICATION_TEAM_EMAIL", "team@mozilla.com")
    monkeypatch.delenv("NOTIFICATION_OVERRIDE_EMAIL", raising=False)
    import sendgrid

    monkeypatch.setattr(sendgrid, "SendGridAPIClient", _FakeClient)


def _params(**overrides):
    params = {
        "to": ["dev@mozilla.com"],
        "subject": "build failure",
        "body_markdown": "# Analysis\n\ntext",
        "attach_patch": False,
    }
    params.update(overrides)
    return params


def _addresses(message):
    return [
        address["email"]
        for personalization in message.get()["personalizations"]
        for group in ("to", "cc")
        for address in personalization.get(group, [])
    ]


async def test_sends_to_the_recorded_recipients_and_the_team():
    result = await email_handler.SendEmailHandler().apply(_params(), _ctx())
    assert result.status == "applied"
    assert result.result == {
        "recipients": ["dev@mozilla.com", "team@mozilla.com"],
        "status_code": 202,
    }
    assert _addresses(_FakeClient.sent) == ["dev@mozilla.com", "team@mozilla.com"]


async def test_a_report_with_no_recipients_still_reaches_the_team():
    await email_handler.SendEmailHandler().apply(_params(to=[]), _ctx())
    assert _addresses(_FakeClient.sent) == ["team@mozilla.com"]


async def test_the_override_replaces_every_recipient(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_OVERRIDE_EMAIL", "me@mozilla.com")
    await email_handler.SendEmailHandler().apply(_params(), _ctx())
    assert _addresses(_FakeClient.sent) == ["me@mozilla.com"]


async def test_the_body_is_sent_as_both_text_and_html():
    await email_handler.SendEmailHandler().apply(_params(), _ctx())
    contents = _FakeClient.sent.get()["content"]
    assert contents[0]["type"] == "text/plain"
    assert contents[0]["value"] == "# Analysis\n\ntext"
    assert "<h1>Analysis</h1>" in contents[1]["value"]


async def test_the_inline_diff_and_the_attachment_are_the_same_bytes():
    patch = b"--- a/x\n+++ b/x\n+#include <y>\n"
    await email_handler.SendEmailHandler().apply(
        _params(
            attach_patch=True,
            body_markdown="# Analysis\n\n```diff\n{patch}\n```",
        ),
        _ctx({"changes/changes.patch": patch}),
    )
    sent = _FakeClient.sent.get()
    (attachment,) = sent["attachments"]
    assert attachment["filename"] == "changes.patch"
    assert attachment["disposition"] == "attachment"
    assert base64.b64decode(attachment["content"]) == patch
    body = sent["content"][0]["value"]
    assert body == "# Analysis\n\n```diff\n" + patch.decode().rstrip("\n") + "\n```"


async def test_the_patch_is_read_once_for_both_uses():
    reads = []

    async def download(key):
        reads.append(key)
        return b"+fix\n"

    from hackbot_runtime.actions.handlers import ApplyContext

    ctx = ApplyContext(run_id="r", agent="a", download_artifact=download)
    await email_handler.SendEmailHandler().apply(
        _params(
            attach_patch=True,
            body_markdown="```diff\n{patch}\n```",
        ),
        ctx,
    )
    assert reads == ["changes/changes.patch"]


async def test_a_long_patch_is_truncated_inline_but_attached_whole():
    patch = ("+line\n" * 500).encode()
    await email_handler.SendEmailHandler().apply(
        _params(
            attach_patch=True,
            body_markdown="```diff\n{patch}\n```",
        ),
        _ctx({"changes/changes.patch": patch}),
    )
    sent = _FakeClient.sent.get()
    assert "truncated to 400 of 500 lines" in sent["content"][0]["value"]
    assert base64.b64decode(sent["attachments"][0]["content"]) == patch


async def test_the_built_payload_is_what_sendgrid_can_serialize():
    # `.get()` holding a helper object instead of its value only fails when the
    # SDK serializes the request, which a mocked client never reaches.
    await email_handler.SendEmailHandler().apply(
        _params(attach_patch=True),
        _ctx({"changes/changes.patch": b"diff --git a b"}),
    )
    json.dumps(_FakeClient.sent.get())


async def test_an_unreadable_patch_does_not_lose_the_email():
    result = await email_handler.SendEmailHandler().apply(
        _params(
            attach_patch=True,
            body_markdown="```diff\n{patch}\n```",
        ),
        _ctx(),
    )
    assert result.status == "applied"
    sent = _FakeClient.sent.get()
    assert "attachments" not in sent
    assert "{patch}" not in sent["content"][0]["value"]


async def test_a_body_can_reference_the_patch_without_attaching_it():
    await email_handler.SendEmailHandler().apply(
        _params(body_markdown="```diff\n{patch}\n```"),
        _ctx({"changes/changes.patch": b"+fix\n"}),
    )
    sent = _FakeClient.sent.get()
    assert "attachments" not in sent
    assert "+fix" in sent["content"][0]["value"]


async def test_the_body_is_untouched_when_the_run_recorded_no_patch():
    await email_handler.SendEmailHandler().apply(_params(), _ctx())
    sent = _FakeClient.sent.get()
    assert "attachments" not in sent
    assert sent["content"][0]["value"] == "# Analysis\n\ntext"


async def test_without_sendgrid_configured_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY")
    result = await email_handler.SendEmailHandler().apply(_params(), _ctx())
    assert result.status == "failed"
    assert "SENDGRID_API_KEY" in result.error
    assert _FakeClient.sent is None


async def test_a_sendgrid_error_is_not_a_delivered_email(monkeypatch):
    # Raised, not caught: the applier stamps the action failed with this message.
    import sendgrid

    def _boom(api_key):
        raise RuntimeError("sendgrid is down")

    monkeypatch.setattr(sendgrid, "SendGridAPIClient", _boom)
    with pytest.raises(RuntimeError, match="sendgrid is down"):
        await email_handler.SendEmailHandler().apply(_params(), _ctx())
