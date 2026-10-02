"""Tests for the shared Bugzilla REST client."""

import base64
import json

import httpx
import pytest
from bugzilla_client import (
    Bug,
    BugType,
    BugUpdate,
    BugzillaClient,
    BugzillaError,
    BugzillaSettings,
    FieldChange,
    KeywordsChange,
    NewAttachment,
    NewBug,
    NewComment,
)
from bugzilla_client import client as client_module
from pydantic import ValidationError
from tenacity import wait_none


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Retry at once instead of backing off, to keep the tests fast."""
    monkeypatch.setattr(BugzillaClient.request.retry, "wait", wait_none())


@pytest.fixture
def bugzilla(monkeypatch):
    """Build a client whose HTTP calls are answered by ``handler``, not the network."""

    def make(handler, **settings) -> BugzillaClient:
        real_async_client = httpx.AsyncClient

        def fake_async_client(**kwargs):
            return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(client_module.httpx, "AsyncClient", fake_async_client)
        settings.setdefault("api_key", VALID_KEY)
        return BugzillaClient(BugzillaSettings(**settings))

    return make


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


# Settings


def test_settings_accept_rest_base_url():
    settings = BugzillaSettings(
        api_key=VALID_KEY, url="https://bugzilla.example.com/rest/"
    )
    assert settings.url == "https://bugzilla.example.com"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BUGZILLA_URL", "https://bugzilla.example.com/rest")
    monkeypatch.setenv("BUGZILLA_API_KEY", VALID_KEY)
    settings = BugzillaSettings.from_env()
    assert settings.url == "https://bugzilla.example.com"
    assert settings.api_key == VALID_KEY


async def test_sends_auth_headers_and_uses_rest_base(bugzilla):
    handler, requests = _recorder(httpx.Response(200, json={"version": "20260101.1"}))
    client = bugzilla(handler, url="https://bugzilla.example.com", edge_key="edge")

    assert await client.request("GET", "version") == {"version": "20260101.1"}

    (request,) = requests
    assert str(request.url) == "https://bugzilla.example.com/rest/version"
    assert request.headers["X-Bugzilla-API-Key"] == VALID_KEY
    assert request.headers["Mozilla-Edge-Key"] == "edge"


def test_settings_require_api_key():
    with pytest.raises(ValidationError, match="api_key"):
        BugzillaSettings()


@pytest.mark.parametrize("key", ["a" * 39, "a" * 41], ids=["short", "long"])
def test_settings_reject_wrong_length_api_key(key):
    with pytest.raises(ValidationError, match="api_key"):
        BugzillaSettings(api_key=key)


def test_bug_url(bugzilla):
    client = bugzilla(lambda r: None, url="https://bugzilla.example.com/rest")
    assert client.bug_url(42) == "https://bugzilla.example.com/show_bug.cgi?id=42"


# Errors


async def test_bugzilla_error_body_raises_with_code(bugzilla):
    body = {"error": True, "code": 102, "message": "You are not authorized"}
    handler, _ = _recorder(httpx.Response(401, json=body))
    with pytest.raises(BugzillaError) as exc_info:
        await bugzilla(handler).get_bugs([1])
    assert exc_info.value.code == 102
    assert exc_info.value.status == 401
    assert "not authorized" in str(exc_info.value)


async def test_error_body_with_200_status_still_raises(bugzilla):
    body = {"error": True, "code": 101, "message": "Bug does not exist"}
    handler, _ = _recorder(httpx.Response(200, json=body))
    with pytest.raises(BugzillaError, match="does not exist"):
        await bugzilla(handler).get_bugs([1])


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
        await bugzilla(handler).update_bug(1, BugUpdate(status="NEW"))
    assert exc_info.value.status == response.status_code
    assert f"HTTP {response.status_code}" in str(exc_info.value)


# Retries


async def test_read_retries_on_rate_limit_then_succeeds(bugzilla):
    handler, requests = _recorder(
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(200, json={"version": "1"}),
    )
    assert await bugzilla(handler).request("GET", "version") == {"version": "1"}
    assert len(requests) == 3


async def test_read_retries_on_network_error(bugzilla):
    handler, requests = _recorder(
        httpx.ReadTimeout("slow"), httpx.Response(200, json={"version": "1"})
    )
    assert await bugzilla(handler).request("GET", "version") == {"version": "1"}
    assert len(requests) == 2


async def test_gives_up_after_max_attempts(bugzilla):
    handler, requests = _recorder(*[httpx.Response(503)] * client_module._ATTEMPTS)
    with pytest.raises(BugzillaError) as exc_info:
        await bugzilla(handler).request("GET", "version")
    assert exc_info.value.status == 503
    assert len(requests) == client_module._ATTEMPTS


async def test_write_retries_on_rate_limit_and_connect_error(bugzilla):
    handler, requests = _recorder(
        httpx.Response(429),
        httpx.ConnectError("refused"),
        httpx.Response(200, json=_no_changes(1)),
    )
    await bugzilla(handler).update_bug(1, BugUpdate(status="NEW"))
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
        await bugzilla(handler).update_bug(1, BugUpdate(comment=NewComment(body="hi")))
    assert len(requests) == 1


# Reads


async def test_get_bugs_sends_ids_and_parses_models(bugzilla):
    body = {
        "bugs": [
            {"id": 1, "summary": "one", "keywords": ["crash"], "cf_extra": "kept"},
            {"id": 2, "summary": "two"},
        ]
    }
    handler, requests = _recorder(httpx.Response(200, json=body))

    bugs = await bugzilla(handler).get_bugs([1, 2, 3], include_fields="id,summary")

    assert [b.id for b in bugs] == [1, 2]
    assert bugs[0].keywords == ["crash"]
    # Fields not declared on the model are kept, not dropped.
    assert bugs[0].model_dump()["cf_extra"] == "kept"
    params = requests[0].url.params
    assert params["id"] == "1,2,3"
    assert params["include_fields"] == "id,summary"


async def test_get_bugs_with_no_ids_makes_no_request(bugzilla):
    handler, requests = _recorder()
    assert await bugzilla(handler).get_bugs([]) == []
    assert requests == []


async def test_get_comments_fetches_many_bugs_in_one_request(bugzilla):
    body = {
        "bugs": {
            "1": {"comments": [_comment(10, 1)]},
            "2": {"comments": [_comment(20, 2), _comment(21, 2)]},
        }
    }
    handler, requests = _recorder(httpx.Response(200, json=body))

    comments = await bugzilla(handler).get_comments([1, 2])

    assert {bug_id: [c.id for c in cs] for bug_id, cs in comments.items()} == {
        1: [10],
        2: [20, 21],
    }
    (request,) = requests
    assert request.url.path == "/rest/bug/1/comment"
    assert request.url.params["ids"] == "2"


async def test_get_attachments_excludes_data_by_default(bugzilla):
    body = {"bugs": {"5": [_attachment(100, 5)]}}
    handler, requests = _recorder(httpx.Response(200, json=body))

    (attachment,) = await bugzilla(handler).get_attachments(5)

    assert attachment.id == 100
    assert attachment.data is None
    assert requests[0].url.params["exclude_fields"] == "data"


async def test_get_attachment_returns_none_when_missing(bugzilla):
    handler, _ = _recorder(httpx.Response(200, json={"attachments": {}}))
    assert await bugzilla(handler).get_attachment(100) is None


async def test_get_attachment_with_data(bugzilla):
    encoded = base64.b64encode(b"content").decode()
    body = {"attachments": {"100": _attachment(100, 5, data=encoded)}}
    handler, requests = _recorder(httpx.Response(200, json=body))

    attachment = await bugzilla(handler).get_attachment(100)

    assert attachment is not None
    assert base64.b64decode(attachment.data) == b"content"
    assert "exclude_fields" not in requests[0].url.params


@pytest.mark.parametrize(
    ("users", "expected"), [([{"name": "a@example.com"}], True), ([], False)]
)
async def test_is_user_in_group(users, expected, bugzilla):
    handler, requests = _recorder(httpx.Response(200, json={"users": users}))

    assert await bugzilla(handler).is_user_in_group("a@example.com", "hackers") is (
        expected
    )

    params = requests[0].url.params
    assert params["names"] == "a@example.com"
    assert params["groups"] == "hackers"
    assert params["permissive"] == "1"


# Writes


async def test_update_bug_sends_only_set_fields(bugzilla):
    answer = {
        "bugs": [
            {
                "id": 1,
                "changes": {
                    "status": {"added": "RESOLVED", "removed": "NEW"},
                    "keywords": {"added": "regression", "removed": ""},
                },
            }
        ]
    }
    handler, requests = _recorder(httpx.Response(200, json=answer))
    update = BugUpdate(
        status="RESOLVED",
        resolution="FIXED",
        severity="S2",
        keywords=KeywordsChange(add=["regression"]),
        comment=NewComment(body="done", is_markdown=True),
        cf_status_firefox150="fixed",
    )

    changes = await bugzilla(handler).update_bug(1, update)

    assert changes == {
        "status": FieldChange(added="RESOLVED", removed="NEW"),
        "keywords": FieldChange(added="regression", removed=""),
    }

    (request,) = requests
    assert request.method == "PUT"
    assert request.url.path == "/rest/bug/1"
    # No ``null`` for unset fields; custom ``cf_*`` fields pass through.
    assert json.loads(request.content) == {
        "status": "RESOLVED",
        "resolution": "FIXED",
        "severity": "S2",
        "keywords": {"add": ["regression"]},
        "comment": {"body": "done", "is_markdown": True},
        "cf_status_firefox150": "fixed",
    }


def test_new_bug_passes_custom_fields_through():
    bug = NewBug(
        product="Firefox",
        component="General",
        summary="s",
        version="unspecified",
        description="d",
        type=BugType.task,
        cf_crash_signature="[@ crash]",
    )
    assert bug.model_dump(exclude_none=True)["cf_crash_signature"] == "[@ crash]"


def test_nested_write_models_reject_typos():
    # Would otherwise be dropped, sending ``"keywords": {}``.
    with pytest.raises(ValidationError, match="ad"):
        BugUpdate(keywords={"ad": ["regression"]})


def test_bug_without_id_is_valid():
    # ``include_fields`` may leave ``id`` out.
    assert Bug.model_validate({"summary": "s"}).id is None


def test_new_bug_requires_core_fields():
    with pytest.raises(ValidationError, match="version"):
        NewBug(
            product="Firefox",
            component="General",
            summary="s",
            description="d",
            type=BugType.task,
        )


def test_new_bug_requires_type():
    # BMO rejects a new bug without one (error 135).
    with pytest.raises(ValidationError, match="type"):
        NewBug(
            product="Firefox",
            component="General",
            summary="s",
            version="unspecified",
            description="d",
        )


async def test_create_bug_returns_id(bugzilla):
    handler, requests = _recorder(httpx.Response(200, json={"id": 77}))
    bug = NewBug(
        product="Firefox",
        component="General",
        summary="new",
        version="unspecified",
        description="steps",
        type=BugType.defect,
        keywords=["crash"],
    )

    assert await bugzilla(handler).create_bug(bug) == 77

    assert requests[0].method == "POST"
    assert requests[0].url.path == "/rest/bug"
    assert json.loads(requests[0].content) == {
        "product": "Firefox",
        "component": "General",
        "summary": "new",
        "version": "unspecified",
        "description": "steps",
        "type": "defect",
        "keywords": ["crash"],
    }


async def test_add_attachment_sends_binary_data(bugzilla):
    handler, requests = _recorder(httpx.Response(201, json={"ids": ["1"]}))
    attachment = NewAttachment(
        data=b"\xff\xfe\x00", file_name="a.bin", summary="s", content_type="x/y"
    )
    await bugzilla(handler).add_attachment(5, attachment)
    body = json.loads(requests[0].content)
    assert base64.b64decode(body["data"]) == b"\xff\xfe\x00"


async def test_add_attachment_reads_ids_from_upstream_response(bugzilla):
    handler, _ = _recorder(httpx.Response(201, json={"ids": ["300"]}))
    attachment = NewAttachment(
        data=b"x", file_name="a.txt", summary="a", content_type="text/plain"
    )
    assert await bugzilla(handler).add_attachment(5, attachment) == 300


async def test_add_attachment_encodes_data(bugzilla):
    # BMO's answer: attachments keyed by id, and no ``ids`` list.
    body = {"attachments": {"300": _attachment(300, 5)}}
    handler, requests = _recorder(httpx.Response(201, json=body))
    attachment = NewAttachment(
        data=b"patch",
        file_name="fix.patch",
        summary="Fix",
        content_type="text/plain",
        is_patch=True,
        comment="See patch",
    )

    attachment_id = await bugzilla(handler).add_attachment(5, attachment)

    assert attachment_id == 300
    body = json.loads(requests[0].content)
    assert base64.b64decode(body["data"]) == b"patch"
    assert body["ids"] == [5]
    assert body["is_patch"] is True
    assert body["comment"] == "See patch"
    assert "is_private" not in body


@pytest.mark.parametrize("answer", [{"ids": []}, {"ids": [1, 2]}, {}])
async def test_add_attachment_rejects_unexpected_id_count(bugzilla, answer):
    handler, _ = _recorder(httpx.Response(201, json=answer))
    attachment = NewAttachment(
        data=b"x", file_name="a.txt", summary="a", content_type="text/plain"
    )
    with pytest.raises(BugzillaError, match="Expected one new attachment id"):
        await bugzilla(handler).add_attachment(5, attachment)
