"""The contract between this proxy's errors and the agent's client.

`agent_tools.bugzilla` keys its handling off `BugsyException.code`: 101 is
"endpoint not exposed", 102 is "skip this bug". Those codes only survive if our
bodies parse the way bugsy expects and none trip its login-problem branch, which
is easy to break from this side without noticing.
"""

import bugsy
import pytest
from bugzilla_proxy.app import create_app
from fastapi.testclient import TestClient


class ResponseLike:
    """The parts of a `requests.Response` that bugsy's error path touches."""

    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def handle(response: ResponseLike):
    client = bugsy.Bugsy(api_key="tok", bugzilla_url="https://proxy.example/rest")
    return client._handle_errors(response)


def proxy_error(client: TestClient, path: str, token: str) -> ResponseLike:
    """Make the proxy produce a real error body, then hand it to bugsy."""
    response = client.get(path, headers={"X-Bugzilla-API-Key": token})
    return ResponseLike(response.status_code, response.json())


class TestErrorCodesSurvive:
    def test_an_unexposed_endpoint_reaches_the_agent_as_101(
        self, settings, mint, public_scope
    ):
        client = TestClient(create_app(settings, upstream=_NoUpstream()))
        raw = proxy_error(client, "/rest/user/someone", mint(public_scope))
        with pytest.raises(bugsy.BugsyException) as excinfo:
            handle(raw)
        assert excinfo.value.code == 101

    def test_a_denied_bug_reaches_the_agent_as_102(self, settings, mint, public_scope):
        upstream = _NoUpstream(
            {"bug": {"bugs": [{"id": 1, "groups": ["core-security"]}]}}
        )
        client = TestClient(create_app(settings, upstream=upstream))
        raw = proxy_error(client, "/rest/bug/1/comment", mint(public_scope))
        with pytest.raises(bugsy.BugsyException) as excinfo:
            handle(raw)
        assert excinfo.value.code == 102

    def test_no_error_is_mistaken_for_a_login_failure(
        self, settings, mint, public_scope
    ):
        """No error body may mention an API key or a username and password.

        bugsy turns those into a LoginException, which the tools do not handle.
        """
        client = TestClient(create_app(settings, upstream=_NoUpstream()))
        for path in ("/rest/user/someone", "/rest/bug/1/comment"):
            raw = proxy_error(client, path, mint(public_scope))
            with pytest.raises(bugsy.BugsyException) as excinfo:
                handle(raw)
            assert not isinstance(excinfo.value, bugsy.LoginException)

    def test_a_successful_body_passes_through_untouched(self):
        assert handle(ResponseLike(200, {"bugs": [{"id": 7}]})) == {"bugs": [{"id": 7}]}


class _NoUpstream:
    def __init__(self, responses: dict | None = None) -> None:
        self.responses = responses or {}

    async def get(self, path: str, params: dict) -> dict:
        return self.responses.get(path, {"bugs": []})

    async def aclose(self) -> None:
        return None
