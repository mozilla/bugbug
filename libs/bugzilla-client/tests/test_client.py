"""Tests for the shared Bugzilla REST client."""

import base64
import json

import httpx
import pytest
from bugzilla_client import BugzillaClient, BugzillaError, BugzillaSettings
from bugzilla_client import client as client_module
from tenacity import wait_none


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Retry at once instead of backing off, to keep the tests fast."""
    monkeypatch.setattr(BugzillaClient.request.retry, "wait", wait_none())


@pytest.fixture
async def bugzilla(monkeypatch):
    """Build a client whose HTTP calls are answered by ``handler``, not the network."""
    clients: list[BugzillaClient] = []

    def make(handler, **settings) -> BugzillaClient:
        real_async_client = httpx.AsyncClient

        def fake_async_client(**kwargs):
            return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(client_module.httpx, "AsyncClient", fake_async_client)
        settings.setdefault("api_key", VALID_KEY)
        client = BugzillaClient(BugzillaSettings(**settings))
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.close()


# A syntactically valid Bugzilla API key: 40 letters and digits.
VALID_KEY = "a" * 40


def _recorder(*responses: httpx.Response | Exception):
    """A handler answering with ``responses`` in turn, recording each request."""
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return handler, requests


def _no_changes(bug_id: int) -> dict:
    """A ``PUT /bug/{id}`` answer for an update that changed no field."""
    return {"bugs": [{"id": bug_id, "changes": {}}]}


def _comment(id: int, bug_id: int) -> dict:
    return {
        "id": id,
        "bug_id": bug_id,
        "count": 0,
        "text": "hello",
        "creator": "a@example.com",
        "creation_time": "2026-01-01T00:00:00Z",
    }


def _attachment(id: int, bug_id: int, **extra) -> dict:
    return {
        "id": id,
        "bug_id": bug_id,
        "file_name": "a.txt",
        "summary": "a file",
        "content_type": "text/plain",
        **extra,
    }


async def test_sends_auth_headers_and_uses_rest_base(bugzilla):
    handler, requests = _recorder(httpx.Response(200, json={"version": "20260101.1"}))
    client = bugzilla(handler, url="https://bugzilla.example.com", edge_key="edge")

    assert await client.request("GET", "version") == {"version": "20260101.1"}

    (request,) = requests
    assert str(request.url) == "https://bugzilla.example.com/rest/version"
    assert request.headers["X-Bugzilla-API-Key"] == VALID_KEY
    assert request.headers["Mozilla-Edge-Key"] == "edge"


async def test_context_manager_closes_client():
    async with BugzillaClient(BugzillaSettings(api_key=VALID_KEY)) as client:
        pass
    with pytest.raises(RuntimeError):
        await client.request("GET", "version")


async def test_bugzilla_error_body_raises_with_code(bugzilla):
    body = {"error": True, "code": 102, "message": "You are not authorized"}
    handler, _ = _recorder(httpx.Response(401, json=body))
    with pytest.raises(BugzillaError) as exc_info:
        await bugzilla(handler).bugs.get([1])
    assert exc_info.value.code == 102
    assert exc_info.value.status == 401
    assert "not authorized" in str(exc_info.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>login</html>"),
        httpx.Response(503, text="<html>Service down</html>"),
    ],
    ids=["html-success", "html-error-page"],
)
async def test_non_json_response_raises_with_status(bugzilla, response):
    handler, _ = _recorder(response)
    with pytest.raises(BugzillaError) as exc_info:
        # A write, so the 503 is not retried.
        await bugzilla(handler).bugs.update(1, {"status": "NEW"})
    assert exc_info.value.status == response.status_code
    assert f"HTTP {response.status_code}" in str(exc_info.value)


async def test_read_retries_on_rate_limit_then_succeeds(bugzilla):
    handler, requests = _recorder(
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(200, json={"version": "1"}),
    )
    assert await bugzilla(handler).request("GET", "version") == {"version": "1"}
    assert len(requests) == 3


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(503), httpx.ReadTimeout("slow")],
    ids=["gateway-error", "read-timeout"],
)
async def test_write_not_retried_when_it_may_have_landed(failure, bugzilla):
    # The PUT may have been applied; retrying could post the comment twice.
    handler, requests = _recorder(failure, httpx.Response(200, json=_no_changes(1)))
    with pytest.raises((BugzillaError, httpx.ReadTimeout)):
        await bugzilla(handler).bugs.update(1, {"comment": {"body": "hi"}})
    assert len(requests) == 1


async def test_bugs_get_sends_ids(bugzilla):
    body = {"bugs": [{"id": 1, "summary": "one"}, {"id": 2, "summary": "two"}]}
    handler, requests = _recorder(httpx.Response(200, json=body))

    bugs = await bugzilla(handler).bugs.get([1, 2, 3], include_fields="id,summary")

    assert bugs == body["bugs"]
    params = requests[0].url.params
    assert params["id"] == "1,2,3"
    assert params["include_fields"] == "id,summary"


async def test_comments_get_for_bugs_fetches_many_bugs_in_one_request(bugzilla):
    body = {
        "bugs": {
            "1": {"comments": [_comment(10, 1)]},
            "2": {"comments": [_comment(20, 2), _comment(21, 2)]},
            "3": {"comments": []},
        }
    }
    handler, requests = _recorder(httpx.Response(200, json=body))

    comments = await bugzilla(handler).comments.get_for_bugs([1, 2, 3])

    assert {bug_id: [c["id"] for c in cs] for bug_id, cs in comments.items()} == {
        1: [10],
        2: [20, 21],
        3: [],
    }
    (request,) = requests
    assert request.url.path == "/rest/bug/1/comment"
    # Repeated, not comma-joined: Bugzilla rejects ``ids=2,3``.
    assert request.url.params.get_list("ids") == ["2", "3"]


async def test_comments_get_fetches_many_comments_in_one_request(bugzilla):
    body = {"bugs": {}, "comments": {"10": _comment(10, 1), "20": _comment(20, 2)}}
    handler, requests = _recorder(httpx.Response(200, json=body))

    comments = await bugzilla(handler).comments.get([10, 20])

    assert {comment_id: c["bug_id"] for comment_id, c in comments.items()} == {
        10: 1,
        20: 2,
    }
    (request,) = requests
    assert request.url.path == "/rest/bug/comment/10"
    assert request.url.params.get_list("comment_ids") == ["20"]


async def test_update_bug_returns_changes(bugzilla):
    changes = {"status": {"added": "RESOLVED", "removed": "NEW"}}
    answer = {"bugs": [{"id": 1, "changes": changes}]}
    handler, requests = _recorder(httpx.Response(200, json=answer))
    update = {"status": "RESOLVED", "comment": {"body": "done"}}

    assert await bugzilla(handler).bugs.update(1, update) == changes

    (request,) = requests
    assert request.method == "PUT"
    assert request.url.path == "/rest/bug/1"
    assert json.loads(request.content) == update


async def test_add_attachment_encodes_data(bugzilla):
    # BMO's answer: attachments keyed by id.
    body = {"attachments": {"300": _attachment(300, 5)}}
    handler, requests = _recorder(httpx.Response(201, json=body))

    attachment_id = await bugzilla(handler).attachments.create(
        5,
        b"\xff\xfe\x00",
        {
            "file_name": "fix.patch",
            "summary": "Fix",
            "content_type": "text/plain",
            "is_patch": True,
            "comment": "See patch",
        },
    )

    assert attachment_id == 300
    body = json.loads(requests[0].content)
    assert base64.b64decode(body["data"]) == b"\xff\xfe\x00"
    assert body["ids"] == [5]
    assert body["is_patch"] is True
    assert body["comment"] == "See patch"
