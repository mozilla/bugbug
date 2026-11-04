"""Denials must not reveal more than BMO itself would.

The proxy's credential can see more than a caller could, so anything that
varies with the reason for a denial reports what that credential saw. Each
test here fixes one rule: every reason yields the same answer, and nothing
learned from Bugzilla appears in it.
"""

import httpx
import pytest
from bugzilla_proxy.app import create_app
from bugzilla_proxy.upstream import UpstreamError
from fastapi.testclient import TestClient

SECRET_SUMMARY = "Use-after-free in the secret component"
SECRET_PRODUCT = "SecretProduct"


class Upstream:
    def __init__(self) -> None:
        self.bugs: dict[int, dict] = {}
        self.attachments: dict[int, dict] = {}
        self.refuse_attachments = False

    async def get(self, path: str, params: dict) -> dict:
        if path == "bug":
            wanted = {int(i) for i in str(params.get("id", "")).split(",") if i}
            bugs = [b for i, b in self.bugs.items() if not wanted or i in wanted]
            return {"bugs": bugs}
        if path.startswith("bug/attachment/"):
            if self.refuse_attachments:
                raise UpstreamError("You are not authorized", status_code=401)
            attachment_id = int(path.rsplit("/", 1)[1])
            found = self.attachments.get(attachment_id)
            return {"attachments": {str(attachment_id): found} if found else {}}
        return {"bugs": {}}

    async def open_stream(self, path: str, params: dict) -> httpx.Response:
        return httpx.Response(200, json=await self.get(path, params))

    async def aclose(self) -> None:
        return None


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
        "summary": "A public bug",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def client(settings, upstream) -> TestClient:
    return TestClient(
        create_app(
            settings, privileged_upstream=upstream, unprivileged_upstream=upstream
        )
    )


@pytest.fixture
def scope() -> dict:
    """Core only, comments and attachments, no private access."""
    return {
        "attachments": True,
        "grants": [
            {
                "tier": "full",
                "anchor": {"product": ["Core"]},
                "endpoints": [
                    "bug",
                    "bug/*/comment",
                    "bug/*/attachment",
                    "bug/attachment/*",
                ],
            },
            {"tier": "metadata", "anchor": {"product": ["Tier"]}, "endpoints": ["bug"]},
        ],
    }


# Every way a bug can be unavailable to the scope above.
DENIED_BUGS = {
    "nonexistent": None,
    "private": bug(
        200, groups=["core-security"], summary=SECRET_SUMMARY, product="Core"
    ),
    "outside_anchor": bug(200, product=SECRET_PRODUCT, summary=SECRET_SUMMARY),
    "tier_without_endpoint": bug(200, product="Tier", summary=SECRET_SUMMARY),
}


def response_for(client, mint, scope, path):
    response = client.get(path, headers={"X-Bugzilla-API-Key": mint(scope)})
    return response.status_code, response.json()


def assert_reveals_nothing(body: dict) -> None:
    text = str(body)
    for secret in (SECRET_SUMMARY, SECRET_PRODUCT, "core-security", "Tier"):
        assert secret not in text


class TestEveryReasonLooksTheSame:
    def denied_bug(self, upstream, state):
        if DENIED_BUGS[state] is not None:
            upstream.bugs[200] = DENIED_BUGS[state]

    @pytest.mark.parametrize("state", DENIED_BUGS)
    def test_comments(self, client, mint, scope, upstream, state):
        self.denied_bug(upstream, state)
        status, body = response_for(client, mint, scope, "/rest/bug/200/comment")
        assert (status, body) == (
            404,
            {
                "error": True,
                "code": 102,
                "message": "Bug 200 does not exist or is outside this run's "
                "Bugzilla scope.",
            },
        )
        assert_reveals_nothing(body)

    @pytest.mark.parametrize("state", DENIED_BUGS)
    def test_comment_ids(self, client, mint, scope, upstream, state):
        upstream.bugs[100] = bug(100)
        self.denied_bug(upstream, state)
        status, body = response_for(
            client, mint, scope, "/rest/bug/100/comment?ids=200"
        )
        assert status == 404
        assert body["message"] == (
            "Bug(s) 200 in 'ids' do not exist or are outside this run's Bugzilla "
            "scope. Remove them and retry."
        )
        assert_reveals_nothing(body)

    @pytest.mark.parametrize("state", DENIED_BUGS)
    def test_bug_attachments(self, client, mint, scope, upstream, state):
        self.denied_bug(upstream, state)
        status, body = response_for(client, mint, scope, "/rest/bug/200/attachment")
        assert status == 404
        assert body["message"] == (
            "Bug 200 does not exist or is outside this run's Bugzilla scope."
        )


class TestOneAttachment:
    """Answered as the attachment, never naming the bug it belongs to."""

    EXPECTED = (
        404,
        {
            "error": True,
            "code": 102,
            "message": "Attachment 9 does not exist or is outside this run's "
            "Bugzilla scope.",
        },
    )

    def test_nonexistent(self, client, mint, scope):
        assert response_for(client, mint, scope, "/rest/bug/attachment/9") == (
            self.EXPECTED
        )

    @pytest.mark.parametrize("state", ["private", "outside_anchor"])
    def test_on_a_denied_bug(self, client, mint, scope, upstream, state):
        upstream.bugs[200] = DENIED_BUGS[state]
        upstream.attachments[9] = {"id": 9, "bug_id": 200, "is_private": False}
        result = response_for(client, mint, scope, "/rest/bug/attachment/9")
        assert result == self.EXPECTED
        assert "200" not in result[1]["message"]

    def test_private_on_a_visible_bug(self, client, mint, scope, upstream):
        upstream.bugs[100] = bug(100)
        upstream.attachments[9] = {"id": 9, "bug_id": 100, "is_private": True}
        assert response_for(client, mint, scope, "/rest/bug/attachment/9") == (
            self.EXPECTED
        )

    def test_refused_by_bugzilla(self, client, mint, scope, upstream):
        upstream.refuse_attachments = True
        assert response_for(client, mint, scope, "/rest/bug/attachment/9") == (
            self.EXPECTED
        )


class TestSearchFiltersSilently:
    """BMO's own search leaves out bugs a user cannot see, without saying so."""

    def test_the_response_carries_only_visible_bugs(
        self, client, mint, scope, upstream
    ):
        upstream.bugs[100] = bug(100)
        for state in ("private", "outside_anchor"):
            upstream.bugs[200] = DENIED_BUGS[state]
            status, body = response_for(client, mint, scope, "/rest/bug")
            assert status == 200
            assert set(body) == {"bugs"}, "no counts of what was filtered"
            assert [b["id"] for b in body["bugs"]] == [100]
            assert_reveals_nothing(body)
