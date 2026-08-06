"""Tests for the broker's Phabricator patch route and MCP mounts."""

from unittest.mock import AsyncMock

import pytest
from hackbot_agents.bug_fix import broker
from phabricator_client import (
    PhabricatorDiff,
    PhabricatorSettings,
    UnresolvedCommitError,
)
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

VALID_TOKEN = "api-" + "a" * 28


def _client(fake) -> TestClient:
    route = Route(
        "/phabricator/revision/{revision_id:int}/patch", broker._patch_endpoint(fake)
    )
    return TestClient(Starlette(routes=[route]))


def test_patch_route_returns_base_and_diff():
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(
        return_value=PhabricatorDiff(id=9, base_commit="base9")
    )
    fake.get_raw_diff = AsyncMock(return_value="diff --git a/f b/f\n")
    # The abbreviated base is expanded to a full, fetchable hash.
    fake.resolve_commit = AsyncMock(return_value="base9full")

    resp = _client(fake).get("/phabricator/revision/42/patch")

    assert resp.status_code == 200
    assert resp.json() == {
        "base_commit": "base9full",
        "raw_diff": "diff --git a/f b/f\n",
    }
    fake.get_raw_diff.assert_awaited_once_with(9)
    fake.resolve_commit.assert_awaited_once_with("base9")


def test_patch_route_422_when_base_cannot_be_expanded(caplog):
    # Serving the abbreviation would only fail later in `git fetch`, which
    # reports an exit status and not a reason, so fail here and say why.
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(
        return_value=PhabricatorDiff(id=9, base_commit="base9")
    )
    fake.get_raw_diff = AsyncMock(return_value="diff --git a/f b/f\n")
    fake.resolve_commit = AsyncMock(
        side_effect=UnresolvedCommitError("Cannot expand base9: not imported")
    )

    with caplog.at_level("WARNING", logger=broker.log.name):
        resp = _client(fake).get("/phabricator/revision/42/patch")

    # 422, not 404: the revision and its base exist, they are just unusable.
    assert resp.status_code == 422
    # The reason reaches the agent, which puts the response body in the error
    # it raises, as well as the broker's own log.
    assert resp.json()["error"] == "Cannot expand base9: not imported"
    assert "D42" in caplog.text
    assert "Cannot expand base9: not imported" in caplog.text


def test_patch_route_404_when_no_diff():
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(return_value=None)

    resp = _client(fake).get("/phabricator/revision/42/patch")

    assert resp.status_code == 404


def test_patch_route_404_when_no_base_commit():
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(
        return_value=PhabricatorDiff(id=9, base_commit=None)
    )

    resp = _client(fake).get("/phabricator/revision/42/patch")

    assert resp.status_code == 404


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


def test_app_serves_the_patch_route():
    paths = {r.path for r in _app().routes if isinstance(r, Route)}
    assert "/phabricator/revision/{revision_id:int}/patch" in paths
