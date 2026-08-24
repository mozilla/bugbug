"""End-to-end behaviour of the proxy against a stubbed Bugzilla."""

import pytest
from bugzilla_proxy.app import create_app
from bugzilla_proxy.upstream import UpstreamError
from fastapi.testclient import TestClient


class FakeUpstream:
    """Stands in for BMO, and records what the proxy asked it for."""

    def __init__(self, responses: dict | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []
        self.error: UpstreamError | None = None

    async def get(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        return self.responses.get(path, {"bugs": []})

    async def aclose(self) -> None:
        return None

    def last_params(self, path: str) -> dict:
        for called_path, params in reversed(self.calls):
            if called_path == path:
                return params
        raise AssertionError(f"{path} was never requested")


def bug(**overrides) -> dict:
    payload = {
        "id": 100,
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
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def client(settings, upstream) -> TestClient:
    return TestClient(create_app(settings, upstream=upstream))


def auth(token: str) -> dict:
    return {"X-Bugzilla-API-Key": token}


class TestAuthentication:
    def test_an_unsigned_request_is_refused(self, client):
        response = client.get("/rest/bug")
        assert response.status_code == 401
        assert response.json()["code"] == 102

    def test_garbage_in_the_key_header_is_refused(self, client):
        response = client.get("/rest/bug", headers=auth("not-a-token"))
        assert response.status_code == 401

    def test_healthz_needs_no_token(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}


class TestDefaultDeny:
    def test_an_unexposed_endpoint_is_a_101(self, client, mint, public_scope):
        response = client.get(
            "/rest/user/someone@mozilla.com", headers=auth(mint(public_scope))
        )
        assert response.status_code == 404
        assert response.json()["code"] == 101

    def test_writes_are_refused(self, client, mint, public_scope):
        response = client.post("/rest/bug", headers=auth(mint(public_scope)))
        assert response.status_code == 405
        assert response.json()["code"] == 101

    def test_nothing_reaches_upstream_when_denied(
        self, client, mint, public_scope, upstream
    ):
        client.get("/rest/user/x", headers=auth(mint(public_scope)))
        assert upstream.calls == []


class TestSearch:
    def test_public_bugs_pass_through(self, client, mint, public_scope, upstream):
        upstream.responses["bug"] = {"bugs": [bug(), bug(id=101)]}
        response = client.get("/rest/bug", headers=auth(mint(public_scope)))
        assert [b["id"] for b in response.json()["bugs"]] == [100, 101]

    def test_private_bugs_are_dropped_from_results(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {
            "bugs": [bug(), bug(id=101, groups=["core-security"])]
        }
        response = client.get("/rest/bug", headers=auth(mint(public_scope)))
        assert [b["id"] for b in response.json()["bugs"]] == [100]

    def test_a_bug_outside_the_anchor_is_dropped(self, client, mint, upstream):
        scope = {
            "grants": [
                {
                    "tier": "full",
                    "anchor": {"product": ["Core"]},
                    "endpoints": ["bug"],
                }
            ]
        }
        upstream.responses["bug"] = {"bugs": [bug(), bug(id=101, product="Firefox")]}
        response = client.get("/rest/bug", headers=auth(mint(scope)))
        assert [b["id"] for b in response.json()["bugs"]] == [100]

    def test_auth_fields_are_added_to_the_upstream_query(
        self, client, mint, public_scope, upstream
    ):
        client.get(
            "/rest/bug?include_fields=id,summary", headers=auth(mint(public_scope))
        )
        fields = set(upstream.last_params("bug")["include_fields"].split(","))
        assert {"groups", "product", "id", "summary"} <= fields

    def test_the_caller_only_gets_the_fields_it_asked_for(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get(
            "/rest/bug?include_fields=id,summary", headers=auth(mint(public_scope))
        )
        assert response.json()["bugs"] == [{"id": 100, "summary": "A public bug"}]

    def test_a_metadata_tier_hides_fields_it_needed_for_the_decision(
        self, client, mint, upstream
    ):
        scope = {
            "grants": [
                {
                    "tier": "metadata",
                    "anchor": {"product": ["Core"]},
                    "endpoints": ["bug"],
                }
            ]
        }
        upstream.responses["bug"] = {"bugs": [bug(whiteboard="[secret-plan]")]}
        response = client.get("/rest/bug", headers=auth(mint(scope)))
        served = response.json()["bugs"][0]
        assert "whiteboard" not in served
        assert "groups" not in served
        assert served["summary"] == "A public bug"

    def test_credentials_in_the_query_string_are_stripped(
        self, client, mint, public_scope, upstream
    ):
        client.get(
            "/rest/bug?api_key=smuggled&Bugzilla_token=also",
            headers=auth(mint(public_scope)),
        )
        params = upstream.last_params("bug")
        assert "api_key" not in params
        assert "Bugzilla_token" not in params

    def test_the_search_limit_is_capped(
        self, client, mint, public_scope, upstream, settings
    ):
        client.get("/rest/bug?limit=100000", headers=auth(mint(public_scope)))
        assert upstream.last_params("bug")["limit"] == str(settings.max_search_limit)

    def test_search_is_refused_when_no_grant_exposes_it(self, client, mint):
        scope = {
            "grants": [{"tier": "full", "anchor": {}, "endpoints": ["bug/*/comment"]}]
        }
        response = client.get("/rest/bug", headers=auth(mint(scope)))
        assert response.json()["code"] == 101


class TestComments:
    def test_comments_are_served_for_an_in_scope_bug(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/100/comment"] = {"bugs": {"100": {"comments": []}}}
        response = client.get("/rest/bug/100/comment", headers=auth(mint(public_scope)))
        assert response.status_code == 200
        assert "bugs" in response.json()

    def test_comments_on_a_private_bug_are_refused(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug(groups=["core-security"])]}
        response = client.get("/rest/bug/100/comment", headers=auth(mint(public_scope)))
        assert response.json()["code"] == 102
        assert "bug/100/comment" not in [path for path, _ in upstream.calls]

    def test_a_metadata_tier_cannot_reach_comments(self, client, mint, upstream):
        scope = {"grants": [{"tier": "metadata", "anchor": {}, "endpoints": ["bug"]}]}
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get("/rest/bug/100/comment", headers=auth(mint(scope)))
        assert response.json()["code"] == 102

    def test_a_missing_bug_looks_the_same_as_a_forbidden_one(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": []}
        response = client.get("/rest/bug/100/comment", headers=auth(mint(public_scope)))
        assert response.json()["code"] == 102
        assert "not available" in response.json()["message"]


class TestAttachments:
    def test_attachments_are_refused_unless_the_token_allows_them(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get(
            "/rest/bug/100/attachment", headers=auth(mint(public_scope))
        )
        assert response.json()["code"] == 101

    def test_an_attachment_is_authorized_by_the_bug_behind_it(
        self, client, mint, upstream
    ):
        scope = {
            "attachments": True,
            "grants": [
                {
                    "tier": "full",
                    "anchor": {},
                    "endpoints": ["bug", "bug/attachment/*"],
                }
            ],
        }
        upstream.responses["bug"] = {"bugs": [bug(groups=["core-security"])]}
        upstream.responses["bug/attachment/55"] = {
            "attachments": {"55": {"id": 55, "bug_id": 100, "data": "..."}}
        }
        response = client.get("/rest/bug/attachment/55", headers=auth(mint(scope)))
        assert response.json()["code"] == 102


class TestUpstreamFailures:
    def test_an_upstream_error_becomes_a_502(
        self, client, mint, public_scope, upstream
    ):
        upstream.error = UpstreamError("Bugzilla is unreachable")
        response = client.get("/rest/bug", headers=auth(mint(public_scope)))
        assert response.status_code == 502
        assert response.json()["code"] == 103

    def test_a_credential_complaint_is_not_relayed_as_a_login_error(
        self, client, mint, public_scope, upstream
    ):
        """Bugsy turns any message mentioning an API key into a LoginException."""
        upstream.error = UpstreamError("The API key you supplied is invalid")
        response = client.get("/rest/bug", headers=auth(mint(public_scope)))
        assert "API key" not in response.json()["message"]
