"""The two upstream credentials, and the edge key both carry."""

import asyncio

import httpx
import pytest
from bugzilla_proxy.app import create_app
from bugzilla_proxy.upstream import Upstream
from fastapi.testclient import TestClient


def test_identical_keys_are_refused_at_startup(settings):
    """The unprivileged key is the backstop against a proxy bug, so must differ."""
    settings.bugzilla_unprivileged_api_key = settings.bugzilla_privileged_api_key
    with pytest.raises(ValueError, match="same"):
        create_app(settings)


def test_each_client_sends_its_own_key_and_the_edge_key(settings):
    settings.bugzilla_edge_key = "edge-secret"
    app = create_app(settings)
    privileged = app.state.backends["privileged"].upstream._client.headers
    unprivileged = app.state.backends["unprivileged"].upstream._client.headers
    assert privileged["X-Bugzilla-API-Key"] == "privileged-key"
    assert unprivileged["X-Bugzilla-API-Key"] == "unprivileged-key"
    assert privileged["Mozilla-Edge-Key"] == "edge-secret"
    assert unprivileged["Mozilla-Edge-Key"] == "edge-secret"


def test_no_edge_header_without_an_edge_key(settings):
    settings.bugzilla_edge_key = ""
    assert "Mozilla-Edge-Key" not in Upstream(settings, "k")._client.headers


def test_a_stream_carries_its_params_and_both_keys(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"attachments": {}})

    settings.bugzilla_edge_key = "edge"
    upstream = Upstream(settings, "the-key")
    upstream._client._transport = httpx.MockTransport(handler)

    async def go():
        response = await upstream.open_stream(
            "bug/attachment/5", {"exclude_fields": "data"}
        )
        await response.aclose()

    asyncio.run(go())
    assert seen["url"].endswith("/rest/bug/attachment/5?exclude_fields=data")
    assert seen["headers"]["x-bugzilla-api-key"] == "the-key"
    assert seen["headers"]["mozilla-edge-key"] == "edge"


def test_a_missing_key_leaves_its_client_unset(settings):
    settings.bugzilla_unprivileged_api_key = ""
    assert create_app(settings).state.backends["unprivileged"] is None


def test_each_credential_has_its_own_decision_cache(settings):
    """The keys see different bugs, so one's answers must not decide for the other."""
    backends = create_app(settings).state.backends
    assert backends["privileged"].bug_cache is not backends["unprivileged"].bug_cache


PRIVATE_SCOPE = {
    "confidential": True,
    "grants": [
        {
            "tier": "full",
            "anchor": {"groups": ["core-security"], "static_bugs": [1]},
            "endpoints": ["bug"],
        }
    ],
}


@pytest.mark.parametrize(
    ("missing", "scope_fixture", "reach"),
    [
        ("bugzilla_unprivileged_api_key", "public", "without"),
        ("bugzilla_privileged_api_key", "private", "with"),
    ],
)
def test_a_missing_key_is_a_clear_503(
    settings, mint, public_scope, missing, scope_fixture, reach
):
    setattr(settings, missing, "")
    scope = public_scope if scope_fixture == "public" else PRIVATE_SCOPE
    client = TestClient(create_app(settings))
    response = client.get("/rest/bug", headers={"X-Bugzilla-API-Key": mint(scope)})
    assert response.status_code == 503
    message = response.json()["message"]
    assert missing.removeprefix("bugzilla_").removesuffix("_api_key") in message
    assert f"{reach} private grants" in message
