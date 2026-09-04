"""Tests for the Bugzilla user client, on BMO's captured payload shapes."""

import httpx
from app.bugzilla_client import BugzillaUserClient


def _user_client(monkeypatch, json_body: dict) -> tuple[BugzillaUserClient, list]:
    """A client whose HTTP layer replays ``json_body``, capturing requests."""
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
    client = BugzillaUserClient("https://bugzilla.example.com")
    return client, requests


async def test_is_user_in_group_true_for_member(monkeypatch):
    client, requests = _user_client(
        monkeypatch, {"users": [{"name": "dev@mozilla.com"}], "faults": []}
    )

    assert await client.is_user_in_group("dev@mozilla.com", 9) is True

    request = requests[0]
    assert request.url.host == "bugzilla.example.com"
    assert request.url.path == "/rest/user"
    assert request.url.params["names"] == "dev@mozilla.com"
    assert request.url.params["group_ids"] == "9"
    assert request.url.params["permissive"] == "1"
    # The membership filter is anonymous: no credential must ever be sent.
    assert "X-Bugzilla-API-Key" not in request.headers


async def test_is_user_in_group_false_for_non_member(monkeypatch):
    # An existing account outside the group is filtered out server-side
    # (live BMO shape: empty ``users``, empty ``faults``).
    client, _ = _user_client(monkeypatch, {"users": [], "faults": []})
    assert await client.is_user_in_group("outsider@example.com", 9) is False


async def test_is_user_in_group_false_for_unknown_user(monkeypatch):
    # With permissive=1, BMO reports an unknown login as a 200 with the error
    # in ``faults`` and an empty ``users`` list (live BMO shape).
    client, _ = _user_client(
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
    assert await client.is_user_in_group("ghost@example.com", 9) is False
