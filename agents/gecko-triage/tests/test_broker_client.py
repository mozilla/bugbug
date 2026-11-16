"""Tests for the one broker session the state read, the target read and the searches use."""

import asyncio

import pytest
from conftest import fake_broker
from hackbot_agents.gecko_triage.broker_client import broker_session
from hackbot_agents.gecko_triage.dupes import broker_search


async def test_a_call_gives_the_parsed_reply(monkeypatch):
    calls = fake_broker(monkeypatch, {"get_bugs": {"bugs": [{"id": 1}]}})
    async with broker_session("http://b/mcp", 5) as call:
        assert await call("get_bugs", {"ids": [1]}) == {"bugs": [{"id": 1}]}
    assert calls == [("connect", "http://b/mcp"), ("get_bugs", {"ids": [1]})]


async def test_an_error_reply_raises(monkeypatch):
    fake_broker(monkeypatch, {"get_bugs": RuntimeError("access_denied")})
    with pytest.raises(RuntimeError, match="access_denied"):
        async with broker_session("http://b/mcp", 5) as call:
            await call("get_bugs", {"ids": [1]})


async def test_a_hung_call_ends_at_the_timeout(monkeypatch):
    async def hang(args):
        await asyncio.sleep(10)

    fake_broker(monkeypatch, {"get_bugs": hang})
    with pytest.raises(TimeoutError):
        async with broker_session("http://b/mcp", 0.01) as call:
            await call("get_bugs", {"ids": [1]})


async def test_the_search_gives_the_bugs_of_the_reply(monkeypatch):
    calls = fake_broker(monkeypatch, {"search_bugs": {"bugs": [{"id": 2}]}})
    async with broker_search("http://b/mcp", 5) as search:
        assert await search({"product": "Core"}) == [{"id": 2}]
    assert calls[-1] == ("search_bugs", {"params": {"product": "Core"}})
