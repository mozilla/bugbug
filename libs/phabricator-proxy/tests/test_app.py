"""Tests for the mountable read-only Conduit proxy."""

import json
import urllib.parse
from unittest.mock import AsyncMock

import httpx
import phabricator_proxy
from fastapi.testclient import TestClient
from phabricator_client import PhabricatorClient, PhabricatorSettings
from phabricator_client import client as client_module
from phabricator_proxy import app as proxy_app
from phabricator_proxy import create_app

VALID_TOKEN = "api-" + "a" * 28
ALLOWED_METHOD = "differential.revision.search"


def _client(phabricator_client, **kwargs) -> TestClient:
    return TestClient(create_app(phabricator_client, **kwargs))


def _conduit_body(params: dict) -> bytes:
    """A url-encoded request body with no Content-Type header.

    Posted with httpx's `content=`, which sets no Content-Type. That is the
    awkward shape the proxy has to cope with — moz-phab's Conduit client sends
    it — and the one `request.form()` would read as empty.
    """
    return urllib.parse.urlencode(
        {"params": json.dumps(params), "output": "json"}
    ).encode()


def test_forwards_an_allow_listed_method():
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(
        return_value={"result": {"data": []}, "error_code": None, "error_info": None}
    )

    resp = _client(fake).post(
        f"/{ALLOWED_METHOD}", content=_conduit_body({"constraints": {"ids": [42]}})
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "result": {"data": []},
        "error_code": None,
        "error_info": None,
    }
    fake.conduit_call.assert_awaited_once_with(
        ALLOWED_METHOD, {"constraints": {"ids": [42]}}
    )


def test_refuses_a_method_that_writes(caplog):
    fake = AsyncMock()

    with caplog.at_level("WARNING", logger=proxy_app.log.name):
        resp = _client(fake).post(
            "/differential.revision.edit", content=_conduit_body({})
        )

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ERR-CONDUIT-METHOD-NOT-ALLOWED"
    # Nothing reached Phabricator, so the key was never used.
    fake.conduit_call.assert_not_awaited()
    assert "differential.revision.edit" in caplog.text


def test_allow_list_is_overridable():
    # The default list covers hackbot's checkout; another caller may need
    # different reads, so it is a parameter rather than a hard-coded set.
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(return_value={"result": {}})

    proxy = _client(fake, allowed_methods=frozenset({"user.whoami"}))

    assert proxy.post("/user.whoami", content=_conduit_body({})).status_code == 200
    assert (
        proxy.post(f"/{ALLOWED_METHOD}", content=_conduit_body({})).status_code == 403
    )


def test_substitutes_the_proxys_own_conduit_token(monkeypatch):
    # The whole point: whatever token the caller sends is discarded, so a caller
    # never needs (or gets) a real one.
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["params"] = json.loads(data["params"])
            return httpx.Response(
                200,
                json={"result": {}, "error_code": None},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    real = PhabricatorClient(
        PhabricatorSettings(api_key=VALID_TOKEN, url="https://phab.example.com")
    )

    resp = _client(real).post(
        f"/{ALLOWED_METHOD}",
        content=_conduit_body(
            {"constraints": {}, "__conduit__": {"token": "api-caller-supplied"}}
        ),
    )

    assert resp.status_code == 200
    assert captured["url"] == f"https://phab.example.com/api/{ALLOWED_METHOD}"
    assert captured["params"]["__conduit__"] == {"token": VALID_TOKEN}


def test_relays_a_conduit_error_verbatim():
    # Conduit reports failures in the response body, and the caller's client
    # knows how to read them; passing the envelope through unchanged keeps that
    # working instead of flattening it into an HTTP error.
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(
        return_value={
            "result": None,
            "error_code": "ERR-CONDUIT-CORE",
            "error_info": "No such revision",
        }
    )

    resp = _client(fake).post(f"/{ALLOWED_METHOD}", content=_conduit_body({}))

    assert resp.status_code == 200
    assert resp.json()["error_info"] == "No such revision"


def test_rejects_malformed_params():
    fake = AsyncMock()

    resp = _client(fake).post(f"/{ALLOWED_METHOD}", content=b"params=not-json")

    assert resp.status_code == 400
    fake.conduit_call.assert_not_awaited()


def test_rejects_params_that_are_not_an_object():
    fake = AsyncMock()

    resp = _client(fake).post(f"/{ALLOWED_METHOD}", content=b"params=[1,2]")

    assert resp.status_code == 400
    fake.conduit_call.assert_not_awaited()


def test_reports_an_upstream_failure():
    fake = AsyncMock()
    fake.conduit_call = AsyncMock(side_effect=httpx.ConnectError("refused"))

    resp = _client(fake).post(f"/{ALLOWED_METHOD}", content=_conduit_body({}))

    assert resp.status_code == 502
    assert "refused" in resp.json()["error_info"]


def test_serves_nothing_but_conduit_methods():
    # It fronts a credential, so no docs or schema routes come along for the
    # ride. 405, not 404: `/{method}` matches the path and refuses the GET,
    # which is exactly what "there is no docs handler here" looks like.
    fake = AsyncMock()
    app = create_app(fake)
    proxy = TestClient(app)

    assert app.openapi_url is None
    assert proxy.get("/docs").status_code == 405
    assert proxy.get("/openapi.json").status_code == 405


def test_default_allow_list_holds_only_reads():
    # Pinned deliberately: this is what a caller can reach with the proxy's
    # Conduit key, so widening it should be a conscious edit.
    assert phabricator_proxy.READ_ONLY_METHODS == {
        "differential.revision.search",
        "differential.querydiffs",
        "differential.getrawdiff",
        "diffusion.querycommits",
    }
