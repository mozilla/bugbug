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

    return ApplyContext(run_id="run-1", download_artifact=download)


@pytest.fixture(autouse=True)
def _no_deployment_config(monkeypatch):
    for name in ("SLACK_BOT_TOKEN", "SLACK_CHANNELS"):
        monkeypatch.delenv(name, raising=False)


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


async def test_posts_recorded_message_and_returns_the_timestamp(monkeypatch):
    client = _fake_client(monkeypatch)
    result = await slack_handler.PostMessageHandler().apply(
        {"channel": "#sheriff-notifications", "text": "a test regressed"}, _ctx()
    )
    assert client.calls == [
        {"channel": "#sheriff-notifications", "text": "a test regressed"}
    ]
    assert result.status == "applied"
    assert result.result == {"channel": "C1", "ts": "1700000000.000100"}


async def test_resolves_a_configured_audience_to_its_channel(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNELS", '{"sheriffs": "C123", "default": "C999"}')
    client = _fake_client(monkeypatch)
    await slack_handler.PostMessageHandler().apply(
        {"channel": "sheriffs", "text": "hi"}, _ctx()
    )
    assert client.calls[0]["channel"] == "C123"


async def test_unmapped_audience_falls_back_to_the_default_channel(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNELS", '{"default": "C999"}')
    client = _fake_client(monkeypatch)
    await slack_handler.PostMessageHandler().apply(
        {"channel": "Firefox :: New Tab Page", "text": "hi"}, _ctx()
    )
    assert client.calls[0]["channel"] == "C999"


@pytest.mark.parametrize("channels", ["", "not json", '["#c"]'])
async def test_unusable_channel_map_leaves_the_recorded_channel_alone(
    monkeypatch, channels
):
    monkeypatch.setenv("SLACK_CHANNELS", channels)
    client = _fake_client(monkeypatch)
    await slack_handler.PostMessageHandler().apply(
        {"channel": "#sheriff-notifications", "text": "hi"}, _ctx()
    )
    assert client.calls[0]["channel"] == "#sheriff-notifications"


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
