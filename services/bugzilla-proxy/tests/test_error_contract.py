"""The shape every error body must have for an agent to act on it.

Agents reach this proxy through `agent_tools.bugzilla`, which keys its
handling off the error `code`: 101 is "endpoint not exposed", 102 is "skip this
bug". Three properties have to hold for a code to arrive intact, and all three
are easy to break from this side:

* the body is JSON with `error`, `code` and `message`, as Bugzilla's REST API
  returns them,
* the status is a 4xx, since the client only inspects the body on an error
  status,
* the message mentions neither an API key nor a username and password, since
  the client treats those as a login failure, which the tools do not handle.

Asserted directly against the response rather than by running the agents'
client, which this service does not otherwise depend on.
"""

import pytest
from bugzilla_proxy.app import _CREDENTIAL_PHRASES, create_app
from fastapi.testclient import TestClient

# The service scrubs these because they are about its own credential. They are
# also exactly the phrases the agents' client matches, which is why the list is
# asserted here rather than left as an implementation detail.
LOGIN_TRIGGERS = _CREDENTIAL_PHRASES


class NoUpstream:
    def __init__(self, responses: dict | None = None) -> None:
        self.responses = responses or {}

    async def get(self, path: str, params: dict) -> dict:
        return self.responses.get(path, {"bugs": []})

    async def aclose(self) -> None:
        return None


def error_body(client: TestClient, path: str, token: str) -> tuple[int, dict]:
    response = client.get(path, headers={"X-Bugzilla-API-Key": token})
    return response.status_code, response.json()


def assert_readable_by_agents(status: int, body: dict, code: int) -> None:
    assert 400 <= status < 500, "the client only inspects the body on a 4xx"
    assert body["error"] is True
    assert body["code"] == code
    assert isinstance(body.get("message"), str) and body["message"]
    for trigger in LOGIN_TRIGGERS:
        assert trigger not in body["message"]


class TestErrorCodesSurvive:
    def test_an_invalid_request_reaches_the_agent_as_104(
        self, settings, mint, public_scope
    ):
        client = TestClient(
            create_app(
                settings,
                privileged_upstream=NoUpstream(),
                unprivileged_upstream=NoUpstream(),
            )
        )
        status, body = error_body(client, "/rest/bug?limit=0", mint(public_scope))
        assert_readable_by_agents(status, body, 104)

    def test_an_unexposed_endpoint_reaches_the_agent_as_101(
        self, settings, mint, public_scope
    ):
        client = TestClient(
            create_app(
                settings,
                privileged_upstream=NoUpstream(),
                unprivileged_upstream=NoUpstream(),
            )
        )
        status, body = error_body(client, "/rest/user/someone", mint(public_scope))
        assert_readable_by_agents(status, body, 101)

    def test_a_denied_bug_reaches_the_agent_as_102(self, settings, mint, public_scope):
        upstream = NoUpstream(
            {"bug": {"bugs": [{"id": 1, "groups": ["core-security"]}]}}
        )
        client = TestClient(
            create_app(
                settings, privileged_upstream=upstream, unprivileged_upstream=upstream
            )
        )
        status, body = error_body(client, "/rest/bug/1/comment", mint(public_scope))
        assert_readable_by_agents(status, body, 102)

    def test_a_write_reaches_the_agent_as_101(self, settings, mint, public_scope):
        client = TestClient(
            create_app(
                settings,
                privileged_upstream=NoUpstream(),
                unprivileged_upstream=NoUpstream(),
            )
        )
        response = client.post(
            "/rest/bug", headers={"X-Bugzilla-API-Key": mint(public_scope)}
        )
        assert_readable_by_agents(response.status_code, response.json(), 101)

    def test_an_unauthorized_request_reaches_the_agent_as_102(self, settings):
        client = TestClient(
            create_app(
                settings,
                privileged_upstream=NoUpstream(),
                unprivileged_upstream=NoUpstream(),
            )
        )
        response = client.get("/rest/bug")
        assert_readable_by_agents(response.status_code, response.json(), 102)


class TestCredentialComplaintsAreNotRelayed:
    @pytest.mark.parametrize("complaint", LOGIN_TRIGGERS)
    def test_an_upstream_credential_error_is_replaced(
        self, settings, mint, public_scope, complaint
    ):
        """Relaying it verbatim would surface as a LoginException instead."""
        from bugzilla_proxy.upstream import UpstreamError

        class FailingUpstream(NoUpstream):
            async def get(self, path: str, params: dict) -> dict:
                raise UpstreamError(f"The {complaint} you supplied is invalid")

        client = TestClient(
            create_app(
                settings,
                privileged_upstream=FailingUpstream(),
                unprivileged_upstream=FailingUpstream(),
            )
        )
        response = client.get(
            "/rest/bug", headers={"X-Bugzilla-API-Key": mint(public_scope)}
        )
        assert complaint not in response.json()["message"]
