"""Tests for what a Slack delivery must survive to reach a listener.

Deliberately only the wiring: signature verification, `action_id` routing, how
the Hackbot client reaches the callback, and the status codes Slack sees. What a
callback then does with a delivery is covered directly in
`test_slack_listeners.py`, which needs none of this scaffolding.
"""

import json
import time
from types import SimpleNamespace

import pytest
from app.config import settings
from app.main import app
from app.routers import webhooks
from app.slack.app import build_request_handler
from fastapi.testclient import TestClient
from hackbot_client import RunStatus, TriggeredRun
from slack_sdk.signature import SignatureVerifier
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

SIGNING_SECRET = "test-signing-secret"
RUN_ID = "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6"


class _FakeHackbotClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def trigger_run(self, agent_name, inputs, **_):
        self.calls.append((agent_name, inputs))
        return TriggeredRun(
            run_id=RUN_ID, agent=agent_name, status=RunStatus.pending, is_new=True
        )


@pytest.fixture(autouse=True)
def slack_auth(monkeypatch):
    """Answer the `auth.test` Bolt makes before it will run any listener.

    The app is built with a bot token, which turns on Bolt's authorization
    middleware. Without this the suite would reach the real Slack API and every
    delivery would be turned away before reaching a listener.
    """

    async def auth_test(self, **_):
        # The real response type, not a dict: the middleware reads `headers` off
        # it as well as the identity fields.
        return AsyncSlackResponse(
            client=self,
            http_verb="POST",
            api_url="https://slack.com/api/auth.test",
            req_args={},
            headers={},
            status_code=200,
            data={
                "ok": True,
                "url": "https://mozilla.slack.example/",
                "team": "Mozilla",
                "team_id": "T1",
                "user": "hackbot",
                "user_id": "U0HACKBOT",
                "bot_id": "B0HACKBOT",
            },
        )

    monkeypatch.setattr(AsyncWebClient, "auth_test", auth_test)


@pytest.fixture
def hackbot_client():
    return _FakeHackbotClient()


@pytest.fixture
def client(monkeypatch, hackbot_client):
    monkeypatch.setattr(settings.slack, "signing_secret", SIGNING_SECRET)
    # A handler built for this test, so it picks up the patched signing secret
    # and no state carries over; `app.state` would otherwise hand every test
    # whichever handler the first one happened to build.
    app.dependency_overrides[webhooks.get_slack_handler] = build_request_handler
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: hackbot_client
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _payload(
    *,
    action_id: str = "start_agent_run:bug-fix",
    value: dict | None = None,
    payload_type: str = "block_actions",
) -> dict:
    return {
        "type": payload_type,
        "user": {"id": "U0CLICKER", "username": "clicker"},
        "channel": {"id": "C0TRIAGE"},
        "message": {"ts": "1700000000.000100"},
        "actions": [
            {
                "type": "button",
                "action_id": action_id,
                "value": json.dumps(
                    {"agent_name": "bug-fix", "params": {"bug_id": 1234}}
                    if value is None
                    else value
                ),
            }
        ],
        "response_url": "https://hooks.slack.example/actions/T1/1/abc",
        "trigger_id": "123.456.abc",
        "team": {"id": "T1"},
        "api_app_id": "A1",
    }


def _post(client, payload: dict, *, secret: str = SIGNING_SECRET, timestamp=None):
    body = f"payload={json.dumps(payload)}"
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    signature = SignatureVerifier(secret).generate_signature(
        timestamp=timestamp, body=body
    )
    return client.post(
        "/webhooks/slack",
        content=body.encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
    )


# --- authentication, which is Bolt's ---


def test_rejects_a_wrongly_signed_delivery(client, hackbot_client):
    resp = _post(client, _payload(), secret="not-the-signing-secret")
    assert resp.status_code == 401
    assert hackbot_client.calls == []


def test_rejects_a_delivery_without_the_signature_headers(client, hackbot_client):
    resp = client.post(
        "/webhooks/slack",
        content=b"payload=%7B%7D",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401
    assert hackbot_client.calls == []


def test_rejects_a_replayed_delivery(client, hackbot_client):
    # Correctly signed, but outside the five-minute window Bolt enforces.
    resp = _post(client, _payload(), timestamp=int(time.time()) - 60 * 60)
    assert resp.status_code == 401
    assert hackbot_client.calls == []


# --- routing ---


def test_a_click_reaches_the_listener(client, hackbot_client):
    resp = _post(client, _payload())

    assert resp.status_code == 200
    assert hackbot_client.calls == [("bug-fix", {"bug_id": 1234})]


def test_any_agent_in_the_family_routes_here(client, hackbot_client):
    resp = _post(client, _payload(action_id="start_agent_run:test-repair"))

    assert resp.status_code == 200
    assert len(hackbot_client.calls) == 1


def test_an_action_id_outside_the_family_is_not_routed_here(client, hackbot_client):
    # Slack posts every interaction to one Request URL, so an element this app
    # cannot route means it rendered something it cannot service.
    resp = _post(client, _payload(action_id="submit_feedback"))

    assert resp.status_code == 404
    assert resp.json() == {"error": "unhandled request"}
    assert hackbot_client.calls == []


def test_the_client_reaches_the_listener_through_the_request(client, hackbot_client):
    # The route puts it on Bolt's context; nothing in the listener package
    # imports a factory to get one.
    _post(client, _payload())

    assert hackbot_client.calls, "the listener did not receive a client"


# --- what Slack sees when the delivery is bad ---


def test_a_button_value_that_is_not_the_expected_shape_is_refused(
    client, hackbot_client
):
    resp = _post(client, _payload(value={"params": {"bug_id": 1234}}))

    assert resp.status_code == 500
    assert hackbot_client.calls == []


def test_a_failing_trigger_surfaces_rather_than_being_swallowed(
    client, hackbot_client, monkeypatch
):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("Cloud Run Jobs is unhappy")

    monkeypatch.setattr(hackbot_client, "trigger_run", boom)

    assert _post(client, _payload()).status_code == 500


# --- how the handler is held ---


def test_the_handler_is_built_once_and_kept_on_the_app():
    # App-scoped, not per-request: building one registers every listener.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    first = webhooks.get_slack_handler(request)
    second = webhooks.get_slack_handler(request)

    assert first is second
    assert request.app.state.slack_handler is first


def test_two_apps_do_not_share_a_handler():
    one = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    two = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    assert webhooks.get_slack_handler(one) is not webhooks.get_slack_handler(two)
