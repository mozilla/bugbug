"""Tests for the apply-side Slack handler.

Mocks the Slack client so these exercise the handler's own logic -- channel
routing, result parsing, error handling -- without touching a network.
"""

import pytest
from hackbot_runtime.actions.handlers import ApplyContext, slack_handler
from slack_sdk.errors import SlackApiError


def _ctx():
    async def download(_key):
        raise AssertionError("Slack messages do not use artifacts")

    return ApplyContext(run_id="run-1", agent="test-agent", download_artifact=download)


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)


class _FakeClient:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise SlackApiError(
                f"The request to the Slack API failed: {self._error}",
                {"ok": False, "error": self._error},
            )
        return {"ok": True, "channel": "C1", "ts": "1700000000.000100"}


def _fake_client(monkeypatch, error=None):
    client = _FakeClient(error)
    monkeypatch.setattr(slack_handler, "_client", lambda: client)
    return client


async def test_recorded_blocks_are_posted_with_the_text_as_fallback(monkeypatch):
    client = _fake_client(monkeypatch)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*bug 1*"}}]
    await slack_handler.PostMessageHandler().apply(
        {"channel": "#c", "text": "bug 1", "blocks": blocks}, _ctx()
    )
    call = client.calls[0]
    assert call["blocks"] == blocks
    # `text` still travels with them: Slack uses it for the push notification and
    # wherever blocks are not rendered.
    assert call["text"] == "bug 1"


async def test_posts_to_a_channel_id_as_recorded(monkeypatch):
    client = _fake_client(monkeypatch)
    await slack_handler.PostMessageHandler().apply(
        {"channel": "C0123456789", "text": "hi"}, _ctx()
    )
    assert client.calls[0]["channel"] == "C0123456789"


async def test_a_slack_error_is_not_a_delivered_message(monkeypatch):
    _fake_client(monkeypatch, error="channel_not_found")
    result = await slack_handler.PostMessageHandler().apply(
        {"channel": "#nope", "text": "hi"}, _ctx()
    )
    assert result.status == "failed"
    assert "channel_not_found" in result.error


async def test_missing_token_fails_the_action():
    result = await slack_handler.PostMessageHandler().apply(
        {"channel": "#c", "text": "hi"}, _ctx()
    )
    assert result.status == "failed"
    assert "SLACK_BOT_TOKEN" in result.error
