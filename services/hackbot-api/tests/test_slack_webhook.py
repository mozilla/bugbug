"""The Slack interactions receiver: a click on a hackbot button starts a run once."""

import json

import pytest
from app.auth import require_slack_signature
from app.main import app
from app.routers import slack
from app.routers.slack import button_dedupe_key
from app.slack_webhook import Container
from hackbot_client import RunRef, RunStatus
from pydantic import ValidationError


class _FakeHackbotClient:
    """Records every trigger_run call the route makes."""

    def __init__(self):
        self.calls = []

    async def trigger_run(self, agent_name, inputs, *, dedupe_key=None):
        self.calls.append((agent_name, inputs, dedupe_key))
        return RunRef(
            run_id="d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6",
            agent=agent_name,
            status=RunStatus.pending,
        )


@pytest.fixture
def api_client(client, monkeypatch):
    fake = _FakeHackbotClient()
    app.dependency_overrides[require_slack_signature] = lambda: None
    monkeypatch.setattr(slack, "get_hackbot_client", lambda: fake)
    return fake


def _click(
    *,
    user_id: str = "U123",
    channel_id: str = "C0FFEE",
    message_ts: str = "1758300000.000100",
    action_id: str = "start",
    value: dict | None = None,
) -> dict:
    """A `block_actions` delivery for one click."""
    if value is None:
        value = {
            "type": "start_agent_run",
            "agent_name": "bug-fix",
            "params": {"bug_id": 123},
        }
    return {
        "type": "block_actions",
        "user": {"id": user_id, "username": "user"},
        "container": {
            "type": "message",
            "channel_id": channel_id,
            "message_ts": message_ts,
            "is_ephemeral": False,
        },
        "channel": {"id": channel_id},
        "message": {"ts": message_ts},
        "actions": [{"action_id": action_id, "value": json.dumps(value)}],
        "trigger_id": "trigger-123",
    }


def _post(client, payload: dict):
    return client.post(
        "/webhooks/slack/interactions",
        data={"payload": json.dumps(payload)},
    )


def test_button_dedupe_key_is_the_message_identity():
    container = Container(channel_id="C0FFEE", message_ts="1758300000.000100")
    assert button_dedupe_key(container) == "slack:C0FFEE:1758300000.000100"


def test_start_agent_run_action_triggers_run_keyed_on_the_message(client, api_client):
    response = _post(client, _click())

    assert response.status_code == 200
    assert api_client.calls == [
        ("bug-fix", {"bug_id": 123}, "slack:C0FFEE:1758300000.000100")
    ]


def test_repeated_clicks_on_one_button_carry_the_same_key(client, api_client):
    _post(client, _click(user_id="U123"))
    _post(client, _click(user_id="U123"))
    _post(client, _click(user_id="U999"))

    keys = {key for _, _, key in api_client.calls}
    assert keys == {"slack:C0FFEE:1758300000.000100"}


def test_buttons_on_different_messages_carry_different_keys(client, api_client):
    _post(client, _click(channel_id="C0FFEE", message_ts="1758300000.000100"))
    _post(client, _click(channel_id="C0FFEE", message_ts="1758300000.000200"))
    _post(client, _click(channel_id="CBEEF0", message_ts="1758300000.000100"))

    keys = [key for _, _, key in api_client.calls]
    assert len(set(keys)) == 3


def test_click_without_a_message_container_is_rejected(client, api_client):
    payload = _click()
    payload["container"] = {"type": "view", "view_id": "V123"}

    with pytest.raises(ValidationError):
        _post(client, payload)

    assert api_client.calls == []
