"""Tests for the broker's Phabricator patch route."""

from unittest.mock import AsyncMock

from hackbot_agents.bug_fix import broker
from phabricator_client import PhabricatorDiff, PhabricatorSettings
from starlette.applications import Starlette
from starlette.testclient import TestClient

VALID_TOKEN = "api-" + "a" * 28


def _client(monkeypatch, fake) -> TestClient:
    monkeypatch.setattr(broker, "PhabricatorClient", lambda settings: fake)
    route = broker._phabricator_route(PhabricatorSettings(api_key=VALID_TOKEN))
    return TestClient(Starlette(routes=[route]))


def test_patch_route_returns_base_and_diff(monkeypatch):
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(
        return_value=PhabricatorDiff(id=9, base_commit="base9")
    )
    fake.get_raw_diff = AsyncMock(return_value="diff --git a/f b/f\n")

    resp = _client(monkeypatch, fake).get("/phabricator/revision/42/patch")

    assert resp.status_code == 200
    assert resp.json() == {"base_commit": "base9", "raw_diff": "diff --git a/f b/f\n"}
    fake.get_raw_diff.assert_awaited_once_with(9)


def test_patch_route_404_when_no_diff(monkeypatch):
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(return_value=None)

    resp = _client(monkeypatch, fake).get("/phabricator/revision/42/patch")

    assert resp.status_code == 404


def test_patch_route_404_when_no_base_commit(monkeypatch):
    fake = AsyncMock()
    fake.query_latest_diff = AsyncMock(
        return_value=PhabricatorDiff(id=9, base_commit=None)
    )

    resp = _client(monkeypatch, fake).get("/phabricator/revision/42/patch")

    assert resp.status_code == 404
