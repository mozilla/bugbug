"""Tests for the shared Hackbot client."""

from uuid import UUID

import httpx
import pytest
from hackbot_client import HackbotClient, RunStatus
from hackbot_client import client as client_module
from pydantic import ValidationError

RUN_ID = "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6"


def _client(**kwargs) -> HackbotClient:
    return HackbotClient(
        base_url=kwargs.pop("base_url", "https://hackbot.example"),
        api_key=kwargs.pop("api_key", "secret"),
        **kwargs,
    )


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

        async def post(self, url, json=None, headers=None, params=None):
            captured.update(url=url, json=json, headers=headers, params=params)
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
    # `201`: this request is what started it.
    assert run.is_new is True
    assert captured == {
        "timeout": 30.0,
        "url": "https://hackbot.example/agents/bug-fix/runs",
        "json": {"bug_id": 1234},
        "headers": {
            "X-API-Key": "secret",
            "X-On-Behalf-Of": "user@example.com",
        },
        # No key means no client-assigned id on the create call.
        "params": {},
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


async def test_trigger_run_reports_a_deduplicated_run_as_not_new(monkeypatch):
    # `200` rather than `201`: the key already belonged to this run, so the
    # request that got this answer started nothing.
    _capture_post(
        monkeypatch,
        httpx.Response(
            200,
            json={"run_id": RUN_ID, "agent": "bug-fix", "status": "running"},
        ),
    )

    run = await _client().trigger_run(
        "bug-fix", {"bug_id": 1234}, dedupe_key="push:autoland:abc123"
    )

    assert run.run_id == UUID(RUN_ID)
    assert run.is_new is False


async def test_trigger_run_sends_the_dedupe_key_as_a_query_parameter(monkeypatch):
    captured = _capture_post(
        monkeypatch,
        httpx.Response(
            201,
            json={"run_id": RUN_ID, "agent": "bug-fix", "status": "pending"},
        ),
    )

    await _client().trigger_run(
        "bug-fix", {"bug_id": 1234}, dedupe_key="push:autoland:abc123"
    )

    assert captured["params"] == {"dedupe_key": "push:autoland:abc123"}
