"""Tests for the broker app and its settings."""

import pytest
from agent_tools import bugzilla
from bugzilla_broker import app
from bugzilla_broker.public import public_tools

SETTINGS = {
    "bugzilla_api_url": "https://bugzilla.example.com/rest",
    "bugzilla_api_key": "bz-key",
}


@pytest.mark.parametrize("public_only", [False, True])
def test_the_app_serves_the_tools_of_its_mode(monkeypatch, public_only):
    served = {}

    def fake_build(name, ctx, tools):
        served[name] = tools
        return {"instance": object()}

    monkeypatch.setattr(app, "build_sdk_server", fake_build)
    settings = app.BrokerSettings(**SETTINGS, public_only=public_only)
    built = app.create_app(settings)
    assert [r.path for r in built.routes] == ["/mcp"]
    names = {t.name for t in served["bugzilla"]}
    if public_only:
        assert names == {t.name for t in public_tools()}
    else:
        assert names == {t.name for t in bugzilla.TOOLS}


@pytest.mark.parametrize("public_only", [False, True])
def test_every_served_tool_runs_off_the_event_loop(public_only):
    settings = app.BrokerSettings(**SETTINGS, public_only=public_only)
    for tool in app.served_tools(settings):
        assert getattr(tool.handler, "off_loop", False), tool.name


def test_the_normal_mode_keeps_the_tool_schemas():
    settings = app.BrokerSettings(**SETTINGS)
    for served, original in zip(app.served_tools(settings), bugzilla.TOOLS):
        assert served.name == original.name
        assert served.input_schema == original.input_schema


def test_the_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("BUGZILLA_API_URL", SETTINGS["bugzilla_api_url"])
    monkeypatch.setenv("BUGZILLA_API_KEY", SETTINGS["bugzilla_api_key"])
    monkeypatch.setenv("PUBLIC_ONLY", "1")
    monkeypatch.setenv("COMMENT_SEARCH", "0")
    monkeypatch.setenv("ATTACHMENT_HINT", "Ask for it.")
    settings = app.BrokerSettings()
    assert settings.public_only is True
    assert settings.comment_search is False
    assert settings.attachment_hint == "Ask for it."
    assert settings.port == 8765


@pytest.mark.parametrize(
    "extra", [{"comment_search": False}, {"attachment_hint": "Ask for it."}]
)
def test_the_public_settings_need_public_only(extra):
    with pytest.raises(ValueError, match="need public_only"):
        app.BrokerSettings(**SETTINGS, **extra)
    app.BrokerSettings(**SETTINGS, **extra, public_only=True)
