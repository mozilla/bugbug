"""Tests for the broker's route wiring and inputs.

The Conduit proxy itself is tested in `phabricator-proxy`; what matters here is
that the broker mounts it where the agent expects to find it.
"""

import pytest
from hackbot_agents.bug_fix import broker
from phabricator_client import PhabricatorSettings
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

VALID_TOKEN = "api-" + "a" * 28


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


def test_app_mounts_every_endpoint_the_agent_uses():
    # One MCP server per domain plus the Conduit proxy, all wired here so no
    # token leaves the broker. The proxy path is a contract with the agent:
    # `revision._PROXY_PATH` points moz-phab's Conduit client at it.
    mounts = {r.path for r in _app().routes if isinstance(r, Mount)}
    assert mounts == {"/bugzilla/mcp", "/phabricator/mcp", "/phabricator/api"}


def test_conduit_proxy_answers_where_the_agent_looks_for_it():
    # Mounted, so the method name lands under the mount path rather than
    # needing a route of its own. Refused rather than 404: the proxy is
    # reachable here, it just will not forward a write.
    resp = TestClient(_app()).post("/phabricator/api/differential.revision.edit")

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ERR-CONDUIT-METHOD-NOT-ALLOWED"
