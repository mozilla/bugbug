"""Tests for the Bugzilla broker, which serves the agent-tools Bugzilla tools."""

import asyncio
import threading

from agent_tools import bugzilla
from hackbot_agents.gecko_triage import broker


def test_the_broker_serves_the_read_tools_but_no_download():
    names = {t.name for t in broker.TOOLS}
    assert names == {t.name for t in bugzilla.TOOLS} - {"download_attachment"}
    assert "get_bug_history" in names


def test_the_app_serves_the_tools_with_a_request_timeout(monkeypatch):
    served = {}

    def fake_build(name, ctx, tools):
        served.update(name=name, ctx=ctx, tools=tools)
        return {"instance": object()}

    monkeypatch.setattr(broker, "build_sdk_server", fake_build)
    app = broker.build_app(
        broker.BrokerInputs(
            bugzilla_api_url="https://bugzilla.example.com/rest",
            bugzilla_api_key="bz-key",
        )
    )
    assert [r.path for r in app.routes] == ["/mcp"]
    assert served["name"] == "bugzilla"
    assert served["tools"] == broker.TOOLS
    session = served["ctx"].client.session
    for url in ("https://bugzilla.example.com/rest/bug", "http://localhost/rest"):
        assert isinstance(session.get_adapter(url), broker.TimeoutAdapter), url


def test_a_request_with_no_timeout_gets_the_broker_timeout(monkeypatch):
    sent = []
    monkeypatch.setattr(
        broker.HTTPAdapter, "send", lambda self, request, **kw: sent.append(kw)
    )
    adapter = broker.TimeoutAdapter()
    adapter.send("request")
    adapter.send("request", timeout=5)
    assert [kw["timeout"] for kw in sent] == [broker.BUGZILLA_TIMEOUT, 5]


def test_the_tools_keep_their_schema():
    plain = {t.name: t for t in bugzilla.TOOLS}
    for t in broker.TOOLS:
        assert t.input_schema == plain[t.name].input_schema, t.name
        assert t.description == plain[t.name].description, t.name


async def test_a_tool_call_runs_in_a_worker_thread():
    # bugsy blocks while it waits, so a call must not hold the broker's event loop.
    seen = []

    async def handler(ctx, bug_id: int):
        seen.append(
            (threading.current_thread() is threading.main_thread(), ctx, bug_id)
        )
        return {"bug_id": bug_id}

    assert await broker.in_thread(handler)("ctx", bug_id=7) == {"bug_id": 7}
    assert seen == [(False, "ctx", 7)]


async def test_a_slow_call_does_not_stop_another_call():
    gate = threading.Event()

    async def slow(ctx):
        gate.wait(5)
        return "slow"

    async def fast(ctx):
        gate.set()
        return "fast"

    results = await asyncio.gather(
        broker.in_thread(slow)(None), broker.in_thread(fast)(None)
    )
    assert results == ["slow", "fast"]
