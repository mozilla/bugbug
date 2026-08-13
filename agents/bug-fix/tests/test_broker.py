"""Tests for the broker's read-only Conduit proxy and MCP mounts."""

import json
import urllib.parse
from unittest.mock import AsyncMock

import httpx
import pytest
from hackbot_agents.bug_fix import broker
from phabricator_client import PhabricatorClient, PhabricatorSettings
from phabricator_client import client as client_module
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

VALID_TOKEN = "api-" + "a" * 28
ALLOWED_METHOD = "differential.revision.search"


def _client(phabricator_client) -> TestClient:
    route = Route(
        "/api/{method}",
        broker._conduit_proxy_endpoint(phabricator_client),
        methods=["POST"],
    )
    return TestClient(Starlette(routes=[route]))


def _conduit_body(params: dict) -> bytes:
    """A request body shaped like moz-phab's: url-encoded, no Content-Type.

    Posted with httpx's `content=`, which sets no Content-Type — exactly what
    moz-phab's urllib3-based Conduit client sends.
    """
    return urllib.parse.urlencode(
        {"params": json.dumps(params), "output": "json"}
    ).encode()


def test_proxy_forwards_an_allow_listed_method():
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(
        return_value={"result": {"data": []}, "error_code": None, "error_info": None}
    )

    resp = _client(fake).post(
        f"/api/{ALLOWED_METHOD}", content=_conduit_body({"constraints": {"ids": [42]}})
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "result": {"data": []},
        "error_code": None,
        "error_info": None,
    }
    fake.conduit_call.assert_awaited_once_with(
        ALLOWED_METHOD, {"constraints": {"ids": [42]}}
    )


def test_proxy_refuses_a_method_that_writes(caplog):
    fake = AsyncMock()

    with caplog.at_level("WARNING", logger=broker.log.name):
        resp = _client(fake).post(
            "/api/differential.revision.edit", content=_conduit_body({})
        )

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ERR-CONDUIT-METHOD-NOT-ALLOWED"
    # Nothing reached Phabricator, so the broker's key was never used.
    fake.conduit_call.assert_not_awaited()
    assert "differential.revision.edit" in caplog.text


def test_proxy_substitutes_the_brokers_own_conduit_token(monkeypatch):
    # The whole point of the proxy: whatever token the caller sends is
    # discarded, so the agent never needs (or gets) a real one.
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["params"] = json.loads(data["params"])
            return httpx.Response(
                200,
                json={"result": {}, "error_code": None},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    real = PhabricatorClient(
        PhabricatorSettings(api_key=VALID_TOKEN, url="https://phab.example.com")
    )

    resp = _client(real).post(
        f"/api/{ALLOWED_METHOD}",
        content=_conduit_body(
            {"constraints": {}, "__conduit__": {"token": "api-caller-supplied"}}
        ),
    )

    assert resp.status_code == 200
    assert captured["url"] == f"https://phab.example.com/api/{ALLOWED_METHOD}"
    assert captured["params"]["__conduit__"] == {"token": VALID_TOKEN}


def test_proxy_relays_a_conduit_error_verbatim():
    # Conduit reports failures in the response body, and the caller's client
    # knows how to read them; passing the envelope through unchanged keeps that
    # working instead of flattening it into an HTTP error.
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(
        return_value={
            "result": None,
            "error_code": "ERR-CONDUIT-CORE",
            "error_info": "No such revision",
        }
    )

    resp = _client(fake).post(f"/api/{ALLOWED_METHOD}", content=_conduit_body({}))

    assert resp.status_code == 200
    assert resp.json()["error_info"] == "No such revision"


def test_proxy_rejects_malformed_params():
    fake = AsyncMock()

    resp = _client(fake).post(f"/api/{ALLOWED_METHOD}", content=b"params=not-json")

    assert resp.status_code == 400
    fake.conduit_call.assert_not_awaited()


def test_proxy_reports_an_upstream_failure():
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(side_effect=httpx.ConnectError("refused"))

    resp = _client(fake).post(f"/api/{ALLOWED_METHOD}", content=_conduit_body({}))

    assert resp.status_code == 502
    assert "refused" in resp.json()["error_info"]


def test_allow_list_holds_only_the_reads_the_checkout_needs():
    # Pinned deliberately: this list is what the agent can reach with the
    # broker's Conduit key, so widening it should be a conscious edit.
    assert broker.READ_ONLY_CONDUIT_METHODS == {
        "differential.revision.search",
        "differential.diff.search",
        "differential.getrawdiff",
        "diffusion.querycommits",
    }


def _app() -> Starlette:
    return broker.build_app(
        broker.BrokerInputs(
            bugzilla_api_url="https://bugzilla.example.com/rest",
            bugzilla_api_key="bz-key",
            phabricator=PhabricatorSettings(
                url="https://phab.example.com", api_key=VALID_TOKEN
            ),
        )
    )


def test_inputs_embed_phabricator_config_from_flat_env_names(monkeypatch):
    # env_nested_max_split=1 splits only on the first underscore, so
    # PHABRICATOR_API_KEY lands on phabricator.api_key, not phabricator.api.key,
    # and flat fields keep binding to their own exact env names.
    monkeypatch.setenv("BUGZILLA_API_URL", "https://bugzilla.example.com/rest")
    monkeypatch.setenv("BUGZILLA_API_KEY", "bz-key")
    monkeypatch.setenv("PHABRICATOR_URL", "https://phab.example.com")
    monkeypatch.setenv("PHABRICATOR_API_KEY", VALID_TOKEN)
    monkeypatch.setenv("PHABRICATOR_TIMEOUT_SECONDS", "15")

    inputs = broker.BrokerInputs()

    assert inputs.bugzilla_api_url == "https://bugzilla.example.com/rest"
    assert inputs.bugzilla_api_key == "bz-key"
    assert inputs.phabricator.url == "https://phab.example.com"
    assert inputs.phabricator.api_key == VALID_TOKEN
    assert inputs.phabricator.timeout_seconds == 15


def test_inputs_require_phabricator_config(monkeypatch):
    # Fail at startup rather than serve tools that 401 on every call.
    monkeypatch.setenv("BUGZILLA_API_URL", "https://bugzilla.example.com/rest")
    monkeypatch.setenv("BUGZILLA_API_KEY", "bz-key")
    monkeypatch.delenv("PHABRICATOR_URL", raising=False)
    monkeypatch.delenv("PHABRICATOR_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="phabricator"):
        broker.BrokerInputs()


def test_app_serves_both_mcp_endpoints():
    # One MCP server per domain, both wired here so no token leaves the broker.
    mounts = {r.path for r in _app().routes if isinstance(r, Mount)}
    assert mounts == {"/bugzilla/mcp", "/phabricator/mcp"}


def test_app_serves_the_conduit_proxy_route():
    # At the root, because a Conduit client derives its API URL as `<base>/api/`.
    routes = {r.path: r for r in _app().routes if isinstance(r, Route)}
    assert "/api/{method}" in routes
    assert routes["/api/{method}"].methods == {"POST"}
