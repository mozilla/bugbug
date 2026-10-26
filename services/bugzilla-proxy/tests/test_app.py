"""End-to-end behaviour of the proxy against a stubbed Bugzilla."""

import httpx
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
        self.stream_status = 200

    async def get(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        response = self.responses.get(path, {"bugs": []})
        if path == "bug" and "id" in params:
            # Like BMO, answer an id lookup with only the bugs it named.
            wanted = {int(i) for i in str(params["id"]).split(",")}
            return {"bugs": [b for b in response["bugs"] if b["id"] in wanted]}
        return response

    async def open_stream(self, path: str, params: dict) -> httpx.Response:
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        return httpx.Response(self.stream_status, json=self.responses.get(path, {}))

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
    return TestClient(
        create_app(
            settings, privileged_upstream=upstream, unprivileged_upstream=upstream
        )
    )


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
        assert "malformed" in response.json()["message"]

    def test_the_rejection_says_why(self, client, mint, public_scope):
        expired = mint(public_scope, exp=1_700_000_001, iat=1_700_000_000)
        response = client.get("/rest/bug", headers=auth(expired))
        assert response.status_code == 401
        assert "expired" in response.json()["message"]

    def test_health_needs_no_token(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"message": "Service is healthy", "status": "ok"}


class TestKeySourceOutage:
    def test_an_unreachable_key_source_is_a_503_not_a_401(
        self, sa_settings, upstream, mint_sa, public_scope, served_certs
    ):
        """An outage is worth retrying; a bad token is not."""
        served_certs["fail"] = True
        client = TestClient(
            create_app(
                sa_settings,
                privileged_upstream=upstream,
                unprivileged_upstream=upstream,
            )
        )
        response = client.get("/rest/bug", headers=auth(mint_sa(public_scope)))
        assert response.status_code == 503
        assert response.json()["code"] == 103


class TestVersion:
    """Brokers probe `version` at startup to check their URL and key."""

    def test_a_valid_token_reads_the_version(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["version"] = {"version": "20261026.1", "extra": "x"}
        response = client.get("/rest/version", headers=auth(mint(public_scope)))
        assert response.status_code == 200
        assert response.json() == {"version": "20261026.1"}
        assert upstream.last_params("version") == {}

    def test_it_still_needs_a_token(self, client, upstream):
        response = client.get("/rest/version")
        assert response.status_code == 401
        assert upstream.calls == []


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

    def test_credentials_in_the_query_string_are_refused(
        self, client, mint, public_scope, upstream
    ):
        response = client.get(
            "/rest/bug?api_key=smuggled&Bugzilla_token=also",
            headers=auth(mint(public_scope)),
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == 104
        assert "api_key" in body["message"]
        assert "X-Bugzilla-API-Key" in body["message"]
        assert upstream.calls == []

    @pytest.mark.parametrize(
        ("field", "hint"),
        [
            ("comments", "/rest/bug/{id}/comment"),
            ("description", "/rest/bug/{id}/comment"),
            ("attachments", "/rest/bug/{id}/attachment"),
            ("history", "not exposed"),
        ],
    )
    def test_inline_extras_are_refused_with_where_to_go_instead(
        self, client, mint, public_scope, upstream, field, hint
    ):
        """BMO inlines these on request, bypassing their own endpoints' checks."""
        response = client.get(
            f"/rest/bug?include_fields=id,{field}", headers=auth(mint(public_scope))
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == 104
        assert field in body["message"]
        assert hint in body["message"]
        assert upstream.calls == []

    @pytest.mark.parametrize("group", ["_all", "_extra"])
    def test_groups_that_include_the_extras_are_refused(
        self, client, mint, public_scope, upstream, group
    ):
        response = client.get(
            f"/rest/bug?include_fields={group}", headers=auth(mint(public_scope))
        )
        assert response.status_code == 400
        assert group in response.json()["message"]
        assert upstream.calls == []

    def test_inline_extras_from_upstream_are_still_dropped(
        self, client, mint, public_scope, upstream
    ):
        """Refused up front, but cut from the response too in case BMO sends them."""
        upstream.responses["bug"] = {
            "bugs": [bug(comments=[{"text": "x"}], attachments=[{}], history=[{}])]
        }
        response = client.get(
            "/rest/bug?include_fields=_default", headers=auth(mint(public_scope))
        )
        served = response.json()["bugs"][0]
        assert not set(served) & {"comments", "attachments", "history"}

    def test_excluding_a_decision_field_hides_it_without_denying_bugs(
        self, client, mint, public_scope, upstream
    ):
        """Excluding `groups` must not turn into every bug being denied."""
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get(
            "/rest/bug?exclude_fields=groups,summary", headers=auth(mint(public_scope))
        )
        served = response.json()["bugs"]
        assert [b["id"] for b in served] == [100]
        assert "groups" not in served[0]
        assert "summary" not in served[0]
        assert upstream.last_params("bug")["exclude_fields"] == "summary"


class TestPublicOnlySearchCondition:
    """BMO drops private bugs before the limit, for scopes that see no others."""

    def test_a_public_scope_asks_for_bugs_in_no_group(
        self, client, mint, public_scope, upstream
    ):
        client.get("/rest/bug?product=Core", headers=auth(mint(public_scope)))
        params = upstream.last_params("bug")
        assert params["f1"] == "bug_group"
        assert params["o1"] == "isempty"

    def test_it_is_numbered_after_the_callers_conditions(
        self, client, mint, public_scope, upstream
    ):
        client.get(
            "/rest/bug?f1=keywords&o1=substring&v1=crash&f3=status&o3=equals&v3=NEW",
            headers=auth(mint(public_scope)),
        )
        params = upstream.last_params("bug")
        assert (params["f1"], params["f3"]) == ("keywords", "status")
        assert (params["f4"], params["o4"]) == ("bug_group", "isempty")

    def test_a_private_scope_does_not_add_it(self, client, mint, upstream):
        scope = {
            "confidential": True,
            "grants": [
                {
                    "tier": "full",
                    "anchor": {"groups": ["core-security"], "static_bugs": [100]},
                    "endpoints": ["bug"],
                }
            ],
        }
        client.get("/rest/bug", headers=auth(mint(scope)))
        assert "bug_group" not in upstream.last_params("bug").values()

    def test_results_are_still_checked_if_the_caller_neutralizes_it(
        self, client, mint, public_scope, upstream
    ):
        """`j_top=OR` turns it into an OR; the response check still holds."""
        upstream.responses["bug"] = {
            "bugs": [bug(), bug(id=101, groups=["core-security"])]
        }
        response = client.get(
            "/rest/bug?j_top=OR&f1=bug_id&o1=greaterthan&v1=0",
            headers=auth(mint(public_scope)),
        )
        assert [b["id"] for b in response.json()["bugs"]] == [100]


class TestMetadataTierFields:
    """A token that can only return some fields says which, rather than omitting."""

    @pytest.fixture
    def metadata_scope(self) -> dict:
        return {"grants": [{"tier": "metadata", "anchor": {}, "endpoints": ["bug"]}]}

    def test_a_field_the_tier_hides_is_refused_with_what_it_exposes(
        self, client, mint, metadata_scope, upstream
    ):
        response = client.get(
            "/rest/bug?include_fields=id,whiteboard",
            headers=auth(mint(metadata_scope)),
        )
        assert response.status_code == 400
        message = response.json()["message"]
        assert "whiteboard" in message
        assert "summary" in message, "lists the fields it does expose"
        assert upstream.calls == []

    def test_a_widening_group_is_refused(self, client, mint, metadata_scope):
        response = client.get(
            "/rest/bug?include_fields=_default", headers=auth(mint(metadata_scope))
        )
        assert response.status_code == 400
        assert response.json()["code"] == 104

    def test_exposed_fields_are_served(self, client, mint, metadata_scope, upstream):
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get(
            "/rest/bug?include_fields=id,summary", headers=auth(mint(metadata_scope))
        )
        assert response.json()["bugs"] == [{"id": 100, "summary": "A public bug"}]

    def test_a_limit_above_the_ceiling_is_refused_not_capped(
        self, client, mint, public_scope, upstream, settings
    ):
        """Capping would return fewer rows than asked for without saying so."""
        response = client.get(
            "/rest/bug?limit=100000", headers=auth(mint(public_scope))
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == 104
        assert str(settings.max_search_limit) in body["message"]
        assert "offset" in body["message"]
        assert upstream.calls == []

    def test_an_absent_limit_gets_the_ceiling(
        self, client, mint, public_scope, upstream, settings
    ):
        """As BMO applies its own default when none is given."""
        client.get("/rest/bug", headers=auth(mint(public_scope)))
        assert upstream.last_params("bug")["limit"] == str(settings.max_search_limit)

    def test_a_non_numeric_limit_is_refused(self, client, mint, public_scope):
        response = client.get("/rest/bug?limit=lots", headers=auth(mint(public_scope)))
        assert response.status_code == 400
        assert response.json()["code"] == 104

    @pytest.mark.parametrize("limit", ["0", "-1", "-500"])
    def test_a_limit_below_one_is_refused(
        self, client, mint, public_scope, upstream, limit
    ):
        """0 means unlimited to BMO, which this proxy does not offer."""
        response = client.get(
            f"/rest/bug?limit={limit}", headers=auth(mint(public_scope))
        )
        assert response.status_code == 400
        assert response.json()["code"] == 104
        assert upstream.calls == []

    def test_a_small_limit_is_kept(self, client, mint, public_scope, upstream):
        client.get("/rest/bug?limit=5", headers=auth(mint(public_scope)))
        assert upstream.last_params("bug")["limit"] == "5"

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
        assert "does not exist or is outside" in response.json()["message"]


class TestUpstreamParamsCannotWidenScope:
    """BMO merges query parameters into the call a path maps to.

    Verified against bugzilla.mozilla.org: `bug/35/comment?ids=36` returns bug
    36's comments too, `comment_ids` returns comments from any bug, and
    `bug/attachment/1?ids=35` adds every attachment on bug 35.
    """

    @pytest.fixture
    def attachment_scope(self) -> dict:
        return {
            "attachments": True,
            "grants": [
                {
                    "tier": "full",
                    "anchor": {},
                    "endpoints": [
                        "bug",
                        "bug/*/comment",
                        "bug/*/attachment",
                        "bug/attachment/*",
                    ],
                }
            ],
        }

    def test_an_extra_id_out_of_scope_fails_the_whole_request(
        self, client, mint, public_scope, upstream
    ):
        """As BMO fails the call on an inaccessible id, rather than omitting it."""
        upstream.responses["bug"] = {
            "bugs": [bug(), bug(id=101), bug(id=102, groups=["core-security"])]
        }
        response = client.get(
            "/rest/bug/100/comment?ids=101,102,103", headers=auth(mint(public_scope))
        )
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == 102
        assert "102, 103" in body["message"]
        assert "101" not in body["message"]
        assert "bug/100/comment" not in [path for path, _ in upstream.calls]

    def test_extra_ids_in_scope_are_forwarded(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug(), bug(id=101)]}
        upstream.responses["bug/100/comment"] = {
            "bugs": {"100": {"comments": []}, "101": {"comments": []}}
        }
        response = client.get(
            "/rest/bug/100/comment?ids=101", headers=auth(mint(public_scope))
        )
        assert response.status_code == 200
        assert upstream.last_params("bug/100/comment")["ids"] == "101"
        assert set(response.json()["bugs"]) == {"100", "101"}

    def test_extra_ids_are_authorized_in_one_upstream_call(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug(), bug(id=101), bug(id=102)]}
        client.get(
            "/rest/bug/100/comment?ids=101&ids=102", headers=auth(mint(public_scope))
        )
        lookups = [p for path, p in upstream.calls if path == "bug"]
        assert [p["id"] for p in lookups] == ["100,101,102"]

    def test_unauthorized_bugs_in_the_response_are_dropped(
        self, client, mint, public_scope, upstream
    ):
        """Even if upstream returns more than asked for, only checked bugs leave."""
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/100/comment"] = {
            "bugs": {"100": {"comments": []}, "999": {"comments": ["secret"]}},
            "comments": {"5": {"bug_id": 999, "text": "secret"}},
        }
        response = client.get("/rest/bug/100/comment", headers=auth(mint(public_scope)))
        assert response.json() == {"bugs": {"100": {"comments": []}}}

    def test_comment_ids_is_refused(self, client, mint, public_scope, upstream):
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get(
            "/rest/bug/100/comment?comment_ids=5&new_since=2024-01-01",
            headers=auth(mint(public_scope)),
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == 104
        assert "comment_ids" in body["message"]
        assert "new_since" in body["message"], "lists what is supported"
        assert upstream.calls == []

    def test_supported_comment_params_are_forwarded(
        self, client, mint, public_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        client.get(
            "/rest/bug/100/comment?new_since=2024-01-01",
            headers=auth(mint(public_scope)),
        )
        assert upstream.last_params("bug/100/comment")["new_since"] == "2024-01-01"

    def test_a_malformed_ids_list_is_refused(self, client, mint, public_scope):
        response = client.get(
            "/rest/bug/100/comment?ids=101,abc", headers=auth(mint(public_scope))
        )
        assert response.status_code == 400
        assert response.json()["code"] == 104

    def test_too_many_ids_are_refused(self, client, mint, public_scope, settings):
        ids = ",".join(str(i) for i in range(1, settings.max_search_limit + 1))
        response = client.get(
            f"/rest/bug/100/comment?ids={ids}", headers=auth(mint(public_scope))
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("param", ["attachment_ids=7", "ids=999"])
    def test_bug_attachments_refuse_id_lists(
        self, client, mint, attachment_scope, upstream, param
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        response = client.get(
            f"/rest/bug/100/attachment?{param}&exclude_fields=data",
            headers=auth(mint(attachment_scope)),
        )
        assert response.status_code == 400
        assert response.json()["code"] == 104
        assert "bug/100/attachment" not in [path for path, _ in upstream.calls]

    def test_bug_attachments_drop_other_bugs_from_the_response(
        self, client, mint, attachment_scope, upstream
    ):
        """Even if upstream returns more than asked for, only checked bugs leave."""
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/100/attachment"] = {
            "bugs": {"100": [], "999": [{"id": 7, "bug_id": 999}]},
            "attachments": {"7": {"id": 7, "bug_id": 999}},
        }
        response = client.get(
            "/rest/bug/100/attachment?exclude_fields=data",
            headers=auth(mint(attachment_scope)),
        )
        assert upstream.last_params("bug/100/attachment") == {"exclude_fields": "data"}
        assert response.json() == {"bugs": {"100": []}}

    def test_one_attachment_forwards_only_what_the_caller_may_send(
        self, client, mint, attachment_scope, upstream
    ):
        """The body is relayed unread, so the allowlist is what keeps it to one."""
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/attachment/55"] = {
            "attachments": {"55": {"id": 55, "bug_id": 100, "is_private": False}}
        }
        client.get(
            "/rest/bug/attachment/55?exclude_fields=data",
            headers=auth(mint(attachment_scope)),
        )
        assert upstream.last_params("bug/attachment/55") == {"exclude_fields": "data"}

    def test_one_attachment_refuses_an_ids_list(
        self, client, mint, attachment_scope, upstream
    ):
        response = client.get(
            "/rest/bug/attachment/55?ids=999", headers=auth(mint(attachment_scope))
        )
        assert response.status_code == 400
        assert response.json()["code"] == 104
        assert upstream.calls == []


class TestPrivateCommentsAndAttachments:
    """Private comments and attachments need a grant cleared for private data.

    `is_private` content is visible upstream to insiders whatever the bug's
    groups, so the bug clearing the anchor is not enough.
    """

    @pytest.fixture
    def attachment_scope(self) -> dict:
        return {
            "attachments": True,
            "grants": [
                {
                    "tier": "full",
                    "anchor": {},
                    "endpoints": ["bug", "bug/*/attachment", "bug/attachment/*"],
                }
            ],
        }

    @pytest.fixture
    def private_scope(self) -> dict:
        return {
            "confidential": True,
            "grants": [
                {
                    "tier": "full",
                    "anchor": {"groups": ["core-security"], "static_bugs": [100]},
                    "endpoints": ["bug", "bug/*/comment"],
                }
            ],
        }

    def comments(self, upstream) -> None:
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/100/comment"] = {
            "bugs": {
                "100": {
                    "comments": [
                        {"id": 1, "text": "public", "is_private": False},
                        {"id": 2, "text": "insiders only", "is_private": True},
                        {"id": 3, "text": "unknown"},
                    ]
                }
            }
        }

    def test_private_and_unmarked_comments_are_dropped(
        self, client, mint, public_scope, upstream
    ):
        self.comments(upstream)
        response = client.get("/rest/bug/100/comment", headers=auth(mint(public_scope)))
        served = response.json()["bugs"]["100"]["comments"]
        assert [c["id"] for c in served] == [1]

    def test_a_private_grant_sees_private_comments(
        self, client, mint, private_scope, upstream
    ):
        self.comments(upstream)
        response = client.get(
            "/rest/bug/100/comment", headers=auth(mint(private_scope))
        )
        served = response.json()["bugs"]["100"]["comments"]
        assert [c["id"] for c in served] == [1, 2, 3]

    def test_is_private_is_always_requested(self, client, mint, public_scope, upstream):
        """A caller's narrowing must not hide the field the filter reads."""
        self.comments(upstream)
        client.get(
            "/rest/bug/100/comment?include_fields=text&exclude_fields=is_private",
            headers=auth(mint(public_scope)),
        )
        params = upstream.last_params("bug/100/comment")
        assert params["include_fields"] == "text,is_private"
        assert "exclude_fields" not in params

    def test_is_private_is_not_returned_unless_asked_for(
        self, client, mint, public_scope, upstream
    ):
        """It is added upstream for the filter, not for the caller."""
        self.comments(upstream)
        response = client.get(
            "/rest/bug/100/comment?include_fields=id,text",
            headers=auth(mint(public_scope)),
        )
        served = response.json()["bugs"]["100"]["comments"]
        assert served == [{"id": 1, "text": "public"}]

    def test_is_private_is_returned_when_no_fields_are_named(
        self, client, mint, public_scope, upstream
    ):
        self.comments(upstream)
        response = client.get("/rest/bug/100/comment", headers=auth(mint(public_scope)))
        assert response.json()["bugs"]["100"]["comments"][0]["is_private"] is False

    def test_private_attachments_are_dropped_from_a_bug_listing(
        self, client, mint, attachment_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/100/attachment"] = {
            "bugs": {
                "100": [
                    {"id": 1, "bug_id": 100, "is_private": False},
                    {"id": 2, "bug_id": 100, "is_private": True},
                ]
            }
        }
        response = client.get(
            "/rest/bug/100/attachment", headers=auth(mint(attachment_scope))
        )
        assert [a["id"] for a in response.json()["bugs"]["100"]] == [1]

    def test_a_private_attachment_is_refused_by_id(
        self, client, mint, attachment_scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/attachment/2"] = {
            "attachments": {"2": {"id": 2, "bug_id": 100, "is_private": True}}
        }
        response = client.get(
            "/rest/bug/attachment/2", headers=auth(mint(attachment_scope))
        )
        assert response.json()["code"] == 102


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


class TestStreamedAttachment:
    """Checked on metadata alone, then relayed without being held in memory."""

    @pytest.fixture
    def scope(self) -> dict:
        return {
            "attachments": True,
            "grants": [
                {"tier": "full", "anchor": {}, "endpoints": ["bug", "bug/attachment/*"]}
            ],
        }

    def test_the_check_reads_only_metadata(self, client, mint, scope, upstream):
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/attachment/55"] = {
            "attachments": {"55": {"id": 55, "bug_id": 100, "is_private": False}}
        }
        client.get("/rest/bug/attachment/55", headers=auth(mint(scope)))
        fetches = [p for path, p in upstream.calls if path == "bug/attachment/55"]
        assert fetches == [{"include_fields": "id,bug_id,is_private"}, {}]

    def test_the_body_is_relayed_as_bmo_sent_it(self, client, mint, scope, upstream):
        body = {
            "attachments": {
                "55": {"id": 55, "bug_id": 100, "is_private": False, "data": "aGk="}
            },
            "bugs": {},
        }
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/attachment/55"] = body
        response = client.get("/rest/bug/attachment/55", headers=auth(mint(scope)))
        assert response.status_code == 200
        assert response.json() == body

    @pytest.mark.parametrize(("status", "code"), [(401, 102), (500, 103)])
    def test_bmo_failing_the_full_fetch(
        self, client, mint, scope, upstream, status, code
    ):
        """A refusal is one more unavailable attachment; a 5xx is an outage."""
        upstream.responses["bug"] = {"bugs": [bug()]}
        upstream.responses["bug/attachment/55"] = {
            "attachments": {"55": {"id": 55, "bug_id": 100, "is_private": False}}
        }
        upstream.stream_status = status
        response = client.get("/rest/bug/attachment/55", headers=auth(mint(scope)))
        assert response.json()["code"] == code

    def test_a_denied_attachment_never_has_its_data_fetched(
        self, client, mint, scope, upstream
    ):
        upstream.responses["bug"] = {"bugs": [bug(groups=["core-security"])]}
        upstream.responses["bug/attachment/55"] = {
            "attachments": {"55": {"id": 55, "bug_id": 100, "is_private": False}}
        }
        response = client.get("/rest/bug/attachment/55", headers=auth(mint(scope)))
        assert response.json()["code"] == 102
        fetches = [p for path, p in upstream.calls if path == "bug/attachment/55"]
        assert fetches == [{"include_fields": "id,bug_id,is_private"}]


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
        """The agents' client reads any mention of an API key as a login failure."""
        upstream.error = UpstreamError("The API key you supplied is invalid")
        response = client.get("/rest/bug", headers=auth(mint(public_scope)))
        assert "API key" not in response.json()["message"]
