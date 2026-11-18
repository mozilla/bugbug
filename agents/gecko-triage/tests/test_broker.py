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

    settings = []
    real_settings = broker.BugzillaSettings

    def record_settings(**kwargs):
        settings.append(real_settings(**kwargs))
        return settings[-1]

    monkeypatch.setattr(broker, "build_sdk_server", fake_build)
    monkeypatch.setattr(broker, "BugzillaSettings", record_settings)
    app = broker.build_app(
        broker.BrokerInputs(
            bugzilla_api_url="https://bugzilla.example.com/rest",
            bugzilla_api_key="a" * 40,
        )
    )
    assert [r.path for r in app.routes] == ["/mcp"]
    assert served["name"] == "bugzilla"
    assert served["tools"] == broker.TOOLS
    (used,) = settings
    assert used.api_url == "https://bugzilla.example.com/rest"
    assert used.timeout_seconds == broker.BUGZILLA_TIMEOUT


def test_the_tools_keep_their_schema():
    plain = {t.name: t for t in bugzilla.TOOLS}
    for t in broker.TOOLS:
        assert t.input_schema == plain[t.name].input_schema, t.name
        assert t.description == plain[t.name].description, t.name
