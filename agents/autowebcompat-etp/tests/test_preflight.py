import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest
import requests
from autowebcompat_tools.inputs import BugDataInput, BugIdInput
from autowebcompat_tools.task import RunTracker, TaskConfig
from hackbot_agents.autowebcompat_etp import agent, preflight
from hackbot_agents.autowebcompat_etp.result import (
    PNG_SIGNATURE,
    EtpInvestigationResult,
)

CERT_ERROR = "about:certerror?e=nssBadCert&u=https%3A//example.com/&c=UTF-8"
DNS_ERROR = "about:neterror?e=dnsNotFound&u=https%3A//no-such-host.invalid/"


@pytest.mark.parametrize(
    ("text", "url"),
    [
        (
            "Broken: https://www.example.com/path?q=1.",
            "https://www.example.com/path?q=1",
        ),
        ("see (http://example.org/a) and https://b.example/", "http://example.org/a"),
        ("no link here", None),
        ("ftp://example.com/ only", None),
    ],
)
def test_first_url(text, url):
    assert preflight.first_url(text) == url


def bugzilla_response(json):
    response = mock.Mock()
    response.json.return_value = json
    return response


def test_bug_url(monkeypatch):
    get = mock.Mock(
        return_value=bugzilla_response(
            {"bugs": [{"url": "https://example.com/", "summary": "x"}]}
        )
    )
    monkeypatch.setattr(preflight.requests, "get", get)
    assert preflight.target_url(BugIdInput(bug_id=5)) == "https://example.com/"
    assert get.call_args.args[0] == "https://bugzilla.mozilla.org/rest/bug/5"


@pytest.mark.parametrize(
    "json",
    [
        {"bugs": [{"url": "", "summary": "x"}]},
        # A private bug, read without credentials.
        {"bugs": [], "faults": [{"id": 5, "error": True}]},
        {"bugs": [{"url": "about:blank"}]},
    ],
)
def test_bug_url_none(monkeypatch, json):
    monkeypatch.setattr(
        preflight.requests, "get", mock.Mock(return_value=bugzilla_response(json))
    )
    assert preflight.bug_url(5) is None


def test_bug_url_bugzilla_error(monkeypatch):
    response = bugzilla_response({})
    response.raise_for_status.side_effect = requests.HTTPError("401")
    monkeypatch.setattr(preflight.requests, "get", mock.Mock(return_value=response))
    assert preflight.bug_url(5) is None


@pytest.mark.parametrize(
    ("uri", "error"),
    [
        (CERT_ERROR, "nssBadCert"),
        (DNS_ERROR, "dnsNotFound"),
        ("about:blocked?e=malwareBlocked&u=x", "malwareBlocked"),
        ("about:neterror", ""),
        ("https://example.com/", None),
        ("about:blank", None),
    ],
)
def test_error_page(uri, error):
    assert preflight.error_page(uri) == error


@pytest.mark.parametrize(
    ("uri", "error", "code", "reason", "summary"),
    [
        (
            CERT_ERROR,
            "nssBadCert",
            "SEC_ERROR_UNKNOWN_ISSUER",
            "other",
            "Firefox shows a certificate error (SEC_ERROR_UNKNOWN_ISSUER / "
            "nssBadCert) for https://example.com/, so ETP was not tested.",
        ),
        (
            DNS_ERROR,
            "dnsNotFound",
            None,
            "down",
            "Firefox shows a network error (dnsNotFound) for https://example.com/, "
            "so ETP was not tested.",
        ),
        (
            "about:neterror?e=connectionFailure",
            "connectionFailure",
            None,
            "down",
            None,
        ),
        (
            "about:neterror?e=corruptedContentError",
            "corruptedContentError",
            None,
            "other",
            None,
        ),
    ],
)
def test_error_page_reason(uri, error, code, reason, summary):
    page = preflight.ErrorPage("https://example.com/", uri, error, code, None)
    assert page.failure_reason == reason
    if summary is not None:
        assert page.describe() == summary


def fake_session(document_uri, code=None, navigate_error=False, states=()):
    """A fake MCP session whose tab shows `document_uri`.

    `states`: extra page-state fields, one dict per evaluate_script call (the
    last one repeats).
    """
    remaining = list(states)

    def call_tool(name, args):
        result = mock.Mock(isError=False, content=[mock.Mock(text="ok")])
        if name == "navigate_page" and navigate_error:
            result.isError = True
        elif name == "evaluate_script":
            extra = (
                (remaining.pop(0) if len(remaining) > 1 else remaining[0])
                if remaining
                else {}
            )
            state = {"uri": document_uri, "code": code} | extra
            text = f"```json\n{json.dumps(state)}\n```"
            result.content[0].text = f"Script ran on page and returned:\n{text}"
        elif name == "screenshot_page":
            Path(args["saveTo"]).write_bytes(PNG_SIGNATURE)
        return result

    session = mock.MagicMock()
    session.__aenter__.return_value = session
    session.initialize = mock.AsyncMock()
    session.call_tool = mock.AsyncMock(side_effect=call_tool)
    return session


@pytest.fixture
def mcp(monkeypatch):
    """Patch the MCP client; returns a setter for what the page ends on."""
    client = mock.MagicMock()
    client.__aenter__.return_value = (mock.Mock(), mock.Mock())
    stdio = mock.Mock(return_value=client)
    monkeypatch.setattr(preflight, "stdio_client", stdio)

    def ends_on(*args, **kwargs):
        session = fake_session(*args, **kwargs)
        monkeypatch.setattr(preflight, "ClientSession", mock.Mock(return_value=session))
        return session

    ends_on.stdio = stdio
    return ends_on


SERVER = {"command": "mcp-bin", "args": ["--headless"], "env": {"ETP_PRIVATE": "1"}}


def test_load_page_detects_cert_error(mcp, tmp_path):
    session = mcp(CERT_ERROR, "SEC_ERROR_UNKNOWN_ISSUER", navigate_error=True)
    page = asyncio.run(
        preflight.load_page(SERVER, "https://example.com/", tmp_path / "b.png")
    )
    assert page.error == "nssBadCert"
    assert page.code == "SEC_ERROR_UNKNOWN_ISSUER"
    assert page.screenshot == tmp_path / "b.png"
    assert [c.args[0] for c in session.call_tool.call_args_list] == [
        "navigate_page",
        "wait_for_page_load",
        "evaluate_script",
        "screenshot_page",
    ]
    params = mcp.stdio.call_args.args[0]
    assert (params.command, params.args) == ("mcp-bin", ["--headless"])
    assert params.env["ETP_PRIVATE"] == "1"


def test_load_page_normal_page_proceeds(mcp, tmp_path):
    session = mcp("https://example.com/")
    assert (
        asyncio.run(
            preflight.load_page(SERVER, "https://example.com/", tmp_path / "b.png")
        )
        is None
    )
    assert "screenshot_page" not in [
        c.args[0] for c in session.call_tool.call_args_list
    ]


@pytest.mark.parametrize(
    ("status", "captcha", "reason"),
    [
        (403, False, "blocked"),
        (403, True, "blocked_captcha"),
        (429, False, "blocked"),
        (451, False, "blocked_geo"),
        (451, True, "blocked_geo"),
        (500, False, "down"),
        (503, False, "down"),
        (503, True, "blocked_captcha"),
    ],
)
def test_blocked_page_reason(status, captcha, reason):
    page = preflight.BlockedPage("https://example.com/", status, captcha, None)
    assert page.failure_reason == reason
    assert f"HTTP {status}" in page.describe()


@pytest.mark.parametrize(
    ("status", "blocking"),
    [(200, None), (None, None), (404, None), (401, None), (403, 403), (502, 502)],
)
def test_blocking_status(status, blocking):
    assert preflight.blocking_status({"status": status}) == blocking


def test_load_page_detects_a_blocking_status(mcp, tmp_path):
    session = mcp("https://example.com/", states=[{"status": 403, "captcha": True}])
    page = asyncio.run(
        preflight.load_page(SERVER, "https://example.com/", tmp_path / "b.png")
    )
    assert page.failure_reason == "blocked_captcha"
    assert page.screenshot == tmp_path / "b.png"
    # It looked a second time, after giving the page time to pass a check.
    assert [c.args[0] for c in session.call_tool.call_args_list] == [
        "navigate_page",
        "wait_for_page_load",
        "evaluate_script",
        "wait_for_page_load",
        "evaluate_script",
        "screenshot_page",
    ]


def test_load_page_lets_a_passing_check_through(mcp, tmp_path):
    mcp(
        "https://example.com/",
        states=[{"status": 503, "captcha": True}, {"status": 200}],
    )
    assert (
        asyncio.run(
            preflight.load_page(SERVER, "https://example.com/", tmp_path / "b.png")
        )
        is None
    )


@pytest.mark.parametrize("status", [200, 404, None])
def test_load_page_other_statuses_proceed(mcp, tmp_path, status):
    session = mcp("https://example.com/", states=[{"status": status}])
    assert (
        asyncio.run(
            preflight.load_page(SERVER, "https://example.com/", tmp_path / "b.png")
        )
        is None
    )
    assert [c.args[0] for c in session.call_tool.call_args_list].count(
        "evaluate_script"
    ) == 1


def test_preflight_returns_valid_result_for_a_blocked_site(mcp, tmp_path):
    mcp("https://example.com/", states=[{"status": 403, "captcha": False}])
    result = asyncio.run(
        preflight.preflight(
            BugDataInput(bug_data="broken: https://example.com/"),
            SERVER,
            tmp_path / "b.png",
        )
    )
    assert result.failure_reason == "blocked"
    assert "HTTP 403" in result.summary
    assert result.screenshot_standard == tmp_path / "b.png"


def test_preflight_returns_valid_result(mcp, tmp_path):
    mcp(DNS_ERROR)
    result = asyncio.run(
        preflight.preflight(
            BugDataInput(bug_data="broken: https://no-such-host.invalid/"),
            SERVER,
            tmp_path / "b.png",
        )
    )
    assert result.failure_reason == "down"
    assert result.broken_in_standard is None and result.broken_in_strict is None
    assert result.proposed_exceptions == []
    assert result.screenshot_standard == tmp_path / "b.png"


def test_preflight_without_url_skips(mcp, tmp_path):
    session = mcp(CERT_ERROR)
    assert (
        asyncio.run(
            preflight.preflight(BugDataInput(bug_data="no url"), SERVER, tmp_path / "b")
        )
        is None
    )
    session.call_tool.assert_not_called()


def test_preflight_failure_runs_agent(mcp, tmp_path):
    mcp.stdio.side_effect = OSError("no such binary")
    assert (
        asyncio.run(
            preflight.preflight(
                BugDataInput(bug_data="https://example.com/"), SERVER, tmp_path / "b"
            )
        )
        is None
    )


def test_preflight_timeout_runs_agent(monkeypatch, tmp_path):
    async def hang(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(preflight, "load_page", hang)
    assert (
        asyncio.run(
            preflight.preflight(
                BugDataInput(bug_data="https://example.com/"),
                SERVER,
                tmp_path / "b",
                timeout=0.01,
            )
        )
        is None
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "Environment", mock.MagicMock())
    monkeypatch.setattr(agent, "install_firefox", lambda channel: tmp_path)
    monkeypatch.setattr(
        agent, "build_firefox_devtools_server", mock.Mock(return_value=SERVER)
    )
    monkeypatch.setattr(agent, "build_exceptions_server", mock.Mock())
    monkeypatch.setattr(agent, "build_tracker_lists_server", mock.Mock())
    agent_run = mock.AsyncMock(
        return_value=EtpInvestigationResult(
            summary="works",
            failure_reason="not_reproducible",
            broken_in_standard=False,
            broken_in_strict=False,
            broken_with_easyprivacy=None,
            broken_with_easylist=None,
            culprit_prefs=[],
            blocked_resources=[],
            proposed_exceptions=[],
            exception_verified=False,
            severity="S3",
            priority="P3",
            steps="",
        )
    )
    monkeypatch.setattr(agent.EtpInvestigation, "run", agent_run)

    def run(input_data, **kwargs):
        publish = mock.Mock(side_effect=lambda name, path, kind: f"https://x/{name}")
        result = asyncio.run(
            agent.run_autowebcompat_etp(
                TaskConfig(headless=True),
                RunTracker(),
                input_data,
                {},
                publish,
                **kwargs,
            )
        )
        return result, agent_run

    return run


def test_error_page_stops_before_the_agent(run, mcp, monkeypatch):
    mcp(CERT_ERROR, "SEC_ERROR_UNKNOWN_ISSUER")
    monkeypatch.setattr(preflight, "bug_url", lambda bug_id: "https://example.com/")
    result, agent_run = run(BugIdInput(bug_id=7))
    agent_run.assert_not_called()
    assert result.failure_reason == "other"
    assert result.summary.startswith("Firefox shows a certificate error")
    assert result.screenshot_urls == {"standard": "https://x/screenshot-standard.png"}
    assert result.exception_records == []
    assert (result.whiteboard_tags, result.user_story_lines) == ([], [])
    assert result.existing_exceptions == [] and result.existing_shims == []


@pytest.mark.parametrize(
    ("uri", "kwargs"),
    [("https://example.com/", {}), (CERT_ERROR, {"run_preflight": False})],
)
def test_agent_runs_when_page_loads_or_preflight_is_off(run, mcp, uri, kwargs):
    session = mcp(uri)
    result, agent_run = run(BugDataInput(bug_data="https://example.com/"), **kwargs)
    agent_run.assert_called_once()
    assert result.failure_reason == "not_reproducible"
    assert session.call_tool.called == ("run_preflight" not in kwargs)


def test_agent_inputs_preflight(monkeypatch):
    from hackbot_agents.autowebcompat_etp.__main__ import AgentInputs

    monkeypatch.setenv("BROKER_URL", "http://broker")
    assert AgentInputs().preflight is True
    monkeypatch.setenv("PREFLIGHT", "false")
    assert AgentInputs().preflight is False
