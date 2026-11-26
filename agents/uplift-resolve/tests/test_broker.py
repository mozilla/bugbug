"""Tests for the uplift broker's route wiring and inputs."""

from hackbot_agents.uplift_resolve import broker
from phabricator_client import PhabricatorSettings
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

VALID_TOKEN = "api-" + "a" * 28


def build_test_app() -> Starlette:
    return broker.build_app(
        broker.BrokerInputs(
            bugzilla_api_url="https://bugzilla.example.com/rest",
            bugzilla_api_key="bz-key",
            phabricator=PhabricatorSettings(
                url="https://phab.example.com", api_key=VALID_TOKEN
            ),
        )
    )


def test_inputs_read_bugzilla_and_phabricator_config_from_env(monkeypatch):
    monkeypatch.setenv("BUGZILLA_API_URL", "https://bugzilla.example.com/rest")
    monkeypatch.setenv("BUGZILLA_API_KEY", "bz-key")
    monkeypatch.setenv("PHABRICATOR_URL", "https://phab.example.com")
    monkeypatch.setenv("PHABRICATOR_API_KEY", VALID_TOKEN)

    inputs = broker.BrokerInputs()

    assert inputs.bugzilla_api_url == "https://bugzilla.example.com/rest", (
        "The Bugzilla URL should come from `BUGZILLA_API_URL`, as Terraform sets it."
    )
    assert inputs.bugzilla_api_key == "bz-key", (
        "The Bugzilla key should come from `BUGZILLA_API_KEY`."
    )
    assert inputs.phabricator.api_key == VALID_TOKEN, (
        "The Phabricator key should come from `PHABRICATOR_API_KEY`."
    )


def test_app_mounts_every_endpoint_the_agent_uses():
    mounts = {
        route.path for route in build_test_app().routes if isinstance(route, Mount)
    }

    assert mounts == {"/bugzilla/mcp", "/phabricator/mcp", "/phabricator/api"}, (
        "The broker should serve the Bugzilla and Phabricator MCP tools and the "
        "Conduit proxy the agent fetches diffs through."
    )


def test_conduit_proxy_refuses_writes():
    response = TestClient(build_test_app()).post(
        "/phabricator/api/differential.revision.edit"
    )

    assert response.status_code == 403, (
        "The Conduit proxy should be mounted, and refuse to forward a write."
    )
