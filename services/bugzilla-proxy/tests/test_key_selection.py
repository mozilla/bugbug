"""Which upstream key reads for a token, decided by its scope alone.

A scope that cannot reach private bugs reads with the unprivileged key, from an
account in no groups, so BMO refuses anything private even if a check in the
proxy were wrong. Only a scope with a private grant reads with the privileged
key.
"""

import httpx
import pytest
from bugzilla_proxy.app import create_app
from fastapi.testclient import TestClient


def bug(bug_id: int, **overrides) -> dict:
    payload = {
        "id": bug_id,
        "groups": [],
        "product": "Core",
        "component": "DOM",
        "status": "NEW",
        "resolution": "",
        "keywords": [],
        "whiteboard": "",
        "blocks": [],
        "creation_time": "2024-03-01T10:00:00Z",
        "summary": f"Bug {bug_id}",
    }
    payload.update(overrides)
    return payload


class Bugzilla:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.bugs = [
            bug(100),
            # Visible to the unprivileged account only because it is cc'd,
            # which a public-only scope must still refuse.
            bug(200, groups=["core-security"]),
        ]

    async def get(self, path: str, params: dict) -> dict:
        self.calls.append(path)
        if path == "bug":
            if "id" in params:
                wanted = {int(i) for i in str(params["id"]).split(",")}
                return {"bugs": [b for b in self.bugs if b["id"] in wanted]}
            return {"bugs": list(self.bugs)}
        if path == "version":
            return {"version": "20261027.1"}
        return {"bugs": {}}

    async def open_stream(self, path: str, params: dict) -> httpx.Response:
        return httpx.Response(200, json=await self.get(path, params))

    async def aclose(self) -> None:
        return None


class Untouchable:
    """The key a scope must never be read with."""

    async def get(self, *_a, **_k):
        raise AssertionError("read with the wrong key")

    async def open_stream(self, *_a, **_k):
        raise AssertionError("read with the wrong key")

    async def aclose(self) -> None:
        return None


PUBLIC = {
    "grants": [{"tier": "full", "anchor": {}, "endpoints": ["bug", "bug/*/comment"]}]
}
PUBLIC_ANCHORED = {
    "grants": [{"tier": "full", "anchor": {"product": ["Core"]}, "endpoints": ["bug"]}]
}
PRIVATE_GRANT = {
    "tier": "full",
    "anchor": {"groups": ["core-security"], "static_bugs": [200]},
    "endpoints": ["bug", "bug/*/comment"],
}
PRIVATE = {"confidential": True, "grants": [PRIVATE_GRANT]}
MIXED = {"confidential": True, "grants": [PRIVATE_GRANT, PUBLIC["grants"][0]]}

PATHS = ["/rest/version", "/rest/bug", "/rest/bug/100/comment"]


def client_for(settings, *, privileged, unprivileged) -> TestClient:
    return TestClient(
        create_app(
            settings,
            privileged_upstream=privileged,
            unprivileged_upstream=unprivileged,
        )
    )


@pytest.mark.parametrize("scope", [PUBLIC, PUBLIC_ANCHORED], ids=["any", "anchored"])
def test_a_public_only_scope_reads_with_the_unprivileged_key(settings, mint, scope):
    upstream = Bugzilla()
    client = client_for(settings, privileged=Untouchable(), unprivileged=upstream)
    for path in PATHS:
        client.get(path, headers={"X-Bugzilla-API-Key": mint(scope)})
    assert upstream.calls


@pytest.mark.parametrize("scope", [PRIVATE, MIXED], ids=["private", "mixed"])
def test_a_scope_with_a_private_grant_reads_with_the_privileged_key(
    settings, mint, scope
):
    """Including its public lookups, which the proxy checks bug by bug."""
    upstream = Bugzilla()
    client = client_for(settings, privileged=upstream, unprivileged=Untouchable())
    for path in PATHS:
        client.get(path, headers={"X-Bugzilla-API-Key": mint(scope)})
    assert upstream.calls


def test_a_private_bug_the_unprivileged_account_sees_is_still_denied(settings, mint):
    """If the account is cc'd on a security bug BMO shows it; the scope does not."""
    client = client_for(settings, privileged=Untouchable(), unprivileged=Bugzilla())
    token = mint(PUBLIC)
    response = client.get("/rest/bug", headers={"X-Bugzilla-API-Key": token})
    assert [b["id"] for b in response.json()["bugs"]] == [100]
    response = client.get(
        "/rest/bug/200/comment", headers={"X-Bugzilla-API-Key": token}
    )
    assert response.json()["code"] == 102
