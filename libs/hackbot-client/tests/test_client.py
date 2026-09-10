"""Tests for the shared Hackbot client."""

from uuid import UUID

import httpx
import pytest
from hackbot_client import HackbotClient, RunStatus
from hackbot_client import client as client_module
from pydantic import ValidationError

RUN_ID = "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6"


def _client(**kwargs) -> HackbotClient:
    defaults = {
        "base_url": "https://hackbot.example",
        "api_key": "secret",
    }
    defaults.update(kwargs)
    return HackbotClient(**defaults)


def _capture_post(monkeypatch, response: httpx.Response) -> dict:
    """Stub httpx.AsyncClient to answer with ``response`` and capture the call."""
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            response.request = httpx.Request("POST", url)
            return response

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


async def test_trigger_run_posts_inputs_and_returns_typed_reference(monkeypatch):
    captured = _capture_post(
        monkeypatch,
        httpx.Response(
            201,
            json={"run_id": RUN_ID, "agent": "bug-fix", "status": "pending"},
        ),
    )

    run = await _client(base_url="https://hackbot.example/").trigger_run(
        "bug-fix", {"bug_id": 1234}, on_behalf_of="user@example.com"
    )

    assert run.run_id == UUID(RUN_ID)
    assert run.agent == "bug-fix"
    assert run.status is RunStatus.pending
    assert captured == {
        "timeout": 30.0,
        "url": "https://hackbot.example/agents/bug-fix/runs",
        "json": {"bug_id": 1234},
        "headers": {
            "X-API-Key": "secret",
            "X-On-Behalf-Of": "user@example.com",
        },
    }


async def test_trigger_run_omits_attribution_when_not_provided(monkeypatch):
    captured = _capture_post(
        monkeypatch,
        httpx.Response(
            201,
            json={"run_id": RUN_ID, "agent": "bug-fix", "status": "pending"},
        ),
    )

    await _client().trigger_run("bug-fix", {"bug_id": 1234})

    assert captured["headers"] == {"X-API-Key": "secret"}


async def test_trigger_run_raises_for_http_errors(monkeypatch):
    _capture_post(monkeypatch, httpx.Response(401, json={"detail": "Invalid API key"}))

    with pytest.raises(httpx.HTTPStatusError):
        await _client().trigger_run("bug-fix", {"bug_id": 1234})


async def test_trigger_run_rejects_an_invalid_success_response(monkeypatch):
    _capture_post(monkeypatch, httpx.Response(201, json={"run_id": RUN_ID}))

    with pytest.raises(ValidationError):
        await _client().trigger_run("bug-fix", {"bug_id": 1234})


def test_client_requires_either_api_key_or_audience():
    """Client must have at least one auth method configured."""
    with pytest.raises(ValueError, match="Either api_key or audience must be provided"):
        HackbotClient(base_url="https://example.com")


async def test_trigger_run_with_service_account_auth(monkeypatch):
    """Client uses Bearer token when api_key is not set but audience is."""
    captured = _capture_post(
        monkeypatch,
        httpx.Response(
            201,
            json={"run_id": RUN_ID, "agent": "bug-fix", "status": "pending"},
        ),
    )

    def mock_fetch_id_token(request, audience):
        return "fake.jwt.token"

    monkeypatch.setattr(client_module.id_token, "fetch_id_token", mock_fetch_id_token)

    client = _client(api_key="", audience="https://hackbot.example")
    await client.trigger_run("bug-fix", {"bug_id": 1234})

    assert captured["headers"] == {"Authorization": "Bearer fake.jwt.token"}


async def test_trigger_run_prefers_api_key_over_service_account(monkeypatch):
    """When both api_key and audience are set, use the API key."""
    captured = _capture_post(
        monkeypatch,
        httpx.Response(
            201,
            json={"run_id": RUN_ID, "agent": "bug-fix", "status": "pending"},
        ),
    )

    def mock_fetch_id_token(request, audience, credentials=None):
        raise AssertionError("Should not call fetch_id_token when api_key is set")

    monkeypatch.setattr(client_module.id_token, "fetch_id_token", mock_fetch_id_token)

    client = _client(api_key="secret", audience="https://hackbot.example")
    await client.trigger_run("bug-fix", {"bug_id": 1234})

    assert captured["headers"] == {"X-API-Key": "secret"}
