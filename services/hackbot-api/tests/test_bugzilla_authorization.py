"""Tests for Bugzilla webhook actor authorization."""

from unittest.mock import AsyncMock

import httpx
from app.bugzilla_authorization import AUTHORIZED_GROUP_ID, BugzillaAuthorizer


def _authorizer(member: bool) -> tuple[BugzillaAuthorizer, AsyncMock]:
    """An authorizer whose membership lookup is stubbed to ``member``."""
    authorizer = BugzillaAuthorizer("https://bugzilla.example.com", AUTHORIZED_GROUP_ID)
    lookup = AsyncMock(return_value=member)
    authorizer._is_user_in_group = lookup
    return authorizer, lookup


async def test_is_authorized_caches_positive_lookup():
    authorizer, lookup = _authorizer(member=True)

    assert await authorizer.is_authorized("dev@mozilla.com") is True
    assert await authorizer.is_authorized("dev@mozilla.com") is True
    lookup.assert_awaited_once_with("dev@mozilla.com", AUTHORIZED_GROUP_ID)


async def test_is_authorized_caches_negative_lookup():
    authorizer, lookup = _authorizer(member=False)

    assert await authorizer.is_authorized("someone@example.com") is False
    assert await authorizer.is_authorized("someone@example.com") is False
    lookup.assert_awaited_once_with("someone@example.com", AUTHORIZED_GROUP_ID)


async def test_is_authorized_normalizes_login_case():
    authorizer, lookup = _authorizer(member=True)

    assert await authorizer.is_authorized("Dev@Mozilla.com") is True
    assert await authorizer.is_authorized("dev@mozilla.com") is True
    lookup.assert_awaited_once_with("dev@mozilla.com", AUTHORIZED_GROUP_ID)


# --- the membership lookup itself, on BMO's captured payload shapes ---


def _http_authorizer(
    monkeypatch, json_body: dict
) -> tuple[BugzillaAuthorizer, list[httpx.Request]]:
    """An authorizer whose HTTP layer replays ``json_body``, capturing requests."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=json_body)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    authorizer = BugzillaAuthorizer("https://bugzilla.example.com", AUTHORIZED_GROUP_ID)
    return authorizer, requests


async def test_lookup_authorizes_group_member(monkeypatch):
    authorizer, requests = _http_authorizer(
        monkeypatch, {"users": [{"name": "dev@mozilla.com"}], "faults": []}
    )

    assert await authorizer.is_authorized("dev@mozilla.com") is True

    request = requests[0]
    assert request.url.host == "bugzilla.example.com"
    assert request.url.path == "/rest/user"
    assert request.url.params["names"] == "dev@mozilla.com"
    assert request.url.params["group_ids"] == str(AUTHORIZED_GROUP_ID)
    assert request.url.params["permissive"] == "1"
    # The membership filter is anonymous: no credential must ever be sent.
    assert "X-Bugzilla-API-Key" not in request.headers


async def test_lookup_rejects_non_member(monkeypatch):
    # An existing account outside the group is filtered out server-side
    # (live BMO shape: empty ``users``, empty ``faults``).
    authorizer, _ = _http_authorizer(monkeypatch, {"users": [], "faults": []})
    assert await authorizer.is_authorized("outsider@example.com") is False


async def test_lookup_rejects_unknown_user(monkeypatch):
    # With permissive=1, BMO reports an unknown login as a 200 with the error
    # in ``faults`` and an empty ``users`` list (live BMO shape).
    authorizer, _ = _http_authorizer(
        monkeypatch,
        {
            "users": [],
            "faults": [
                {
                    "error": True,
                    "name": "ghost@example.com",
                    "message": "There is no user named 'ghost@example.com'.",
                }
            ],
        },
    )
    assert await authorizer.is_authorized("ghost@example.com") is False
