"""Tests for the Bugzilla broker, which serves the agent-tools Bugzilla tools."""

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
    assert served["ctx"].timeout == broker.BUGZILLA_TIMEOUT
