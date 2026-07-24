"""Tests for the shared Phabricator Conduit client."""

import json

import httpx
import pytest
from phabricator_client import PhabricatorClient, PhabricatorSettings
from phabricator_client import client as client_module
from pydantic import ValidationError

# A syntactically valid Conduit token: "api-" + 28 [a-z0-9] chars (32 total).
VALID_TOKEN = "api-" + "a" * 28


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def _client(api_key: str = VALID_TOKEN, **kwargs) -> PhabricatorClient:
    return PhabricatorClient(PhabricatorSettings(api_key=api_key, **kwargs))


def _capture_post(monkeypatch, payload: dict) -> dict:
    """Stub httpx.AsyncClient to return `payload`; return a dict capturing the call."""
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["params"] = json.loads(data["params"])
            return _FakeResponse(payload)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


async def test_conduit_request_returns_result(monkeypatch):
    captured = _capture_post(monkeypatch, {"result": {"data": [1, 2]}})
    result = await _client().conduit_request("some.method", foo="bar")
    assert result == {"data": [1, 2]}
    # Token is injected into the request body, not a header.
    assert captured["params"]["__conduit__"] == {"token": VALID_TOKEN}
    assert captured["params"]["foo"] == "bar"
    assert captured["url"].endswith("/api/some.method")
    assert captured["timeout"] == 60


async def test_conduit_request_raises_on_error_code(monkeypatch):
    _capture_post(monkeypatch, {"error_code": "ERR-CONDUIT", "error_info": "nope"})
    with pytest.raises(RuntimeError, match="ERR-CONDUIT"):
        await _client().conduit_request("some.method")


def test_valid_api_key_accepted():
    assert PhabricatorSettings(api_key=VALID_TOKEN).api_key == VALID_TOKEN


def test_empty_api_key_rejected_by_validation():
    # Validation lives in the settings model, not the client.
    with pytest.raises(ValidationError):
        PhabricatorSettings(api_key="")


def test_missing_api_key_rejected_by_validation():
    # PhabricatorSettings is a plain model: no api_key -> required-field error.
    with pytest.raises(ValidationError):
        PhabricatorSettings()


def test_from_env_reads_environment(monkeypatch):
    monkeypatch.setenv("PHABRICATOR_API_KEY", VALID_TOKEN)
    monkeypatch.setenv("PHABRICATOR_URL", "https://phab.env.example.com")
    s = PhabricatorSettings.from_env()
    assert isinstance(s, PhabricatorSettings)
    assert s.api_key == VALID_TOKEN
    assert s.url == "https://phab.env.example.com"


def test_from_env_missing_key_rejected(monkeypatch):
    monkeypatch.delenv("PHABRICATOR_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        PhabricatorSettings.from_env()


def test_api_key_wrong_length_rejected():
    with pytest.raises(ValidationError):
        PhabricatorSettings(api_key="too-short")


async def test_search_transactions(monkeypatch):
    _capture_post(monkeypatch, {"result": {"data": [{"phid": "PHID-XACT-1"}]}})
    assert await _client().search_transactions("PHID-DREV-1") == [
        {"phid": "PHID-XACT-1"}
    ]


async def test_search_revision_found(monkeypatch):
    _capture_post(monkeypatch, {"result": {"data": [{"id": 42}]}})
    assert await _client().search_revision("PHID-DREV-1") == {"id": 42}


async def test_search_revision_missing(monkeypatch):
    _capture_post(monkeypatch, {"result": {"data": []}})
    assert await _client().search_revision("PHID-DREV-1") is None


async def test_query_latest_diff_picks_highest_id(monkeypatch):
    _capture_post(
        monkeypatch,
        {
            "result": {
                "7": {"id": "7", "sourceControlBaseRevision": "old"},
                "9": {"id": "9", "sourceControlBaseRevision": "base9"},
            }
        },
    )
    diff = await _client().query_latest_diff(42)
    assert diff.id == 9
    assert diff.base_commit == "base9"


async def test_query_latest_diff_none_when_no_diffs(monkeypatch):
    _capture_post(monkeypatch, {"result": {}})
    assert await _client().query_latest_diff(42) is None


async def test_get_raw_diff(monkeypatch):
    _capture_post(monkeypatch, {"result": "diff --git a/f b/f\n@@ -1 +1 @@\n-a\n+b\n"})
    assert (await _client().get_raw_diff(9)).startswith("diff --git a/f b/f")


async def test_resolve_commit_returns_full_hash_without_call(monkeypatch):
    # A already-full 40-char hash needs no Conduit round-trip.
    full = "9f2e8c25f0b40fdce8d2f2ca40281e8711815a6d"
    captured = _capture_post(monkeypatch, {"result": {}})
    assert await _client().resolve_commit(full) == full
    assert captured == {}  # no request was made


async def test_resolve_commit_expands_abbreviated_hash(monkeypatch):
    full = "9f2e8c25f0b40fdce8d2f2ca40281e8711815a6d"
    captured = _capture_post(
        monkeypatch,
        {
            "result": {
                "identifierMap": {"9f2e8c25f0b4": "PHID-CMIT-1"},
                "data": {"PHID-CMIT-1": {"identifier": full}},
            }
        },
    )
    assert await _client().resolve_commit("9f2e8c25f0b4") == full
    assert captured["url"].endswith("/api/diffusion.querycommits")
    assert captured["params"]["names"] == ["9f2e8c25f0b4"]


async def test_resolve_commit_returns_none_when_unresolved(monkeypatch):
    _capture_post(monkeypatch, {"result": {"identifierMap": {}, "data": {}}})
    assert await _client().resolve_commit("deadbeef") is None


def test_revision_url_default_base():
    assert _client().revision_url(42) == "https://phabricator.services.mozilla.com/D42"


def test_revision_url_injected_base():
    client = _client(url="https://phab.example.com/")
    assert client.revision_url(7) == "https://phab.example.com/D7"


async def test_custom_timeout_is_passed(monkeypatch):
    captured = _capture_post(monkeypatch, {"result": {}})
    await _client(timeout_seconds=5).conduit_request("some.method")
    assert captured["timeout"] == 5


def test_defaults_from_env_when_no_settings(monkeypatch):
    # No settings passed -> PhabricatorSettings reads the PHABRICATOR_* env vars.
    monkeypatch.setenv("PHABRICATOR_API_KEY", VALID_TOKEN)
    monkeypatch.setenv("PHABRICATOR_URL", "https://phab.env.example.com")
    client = PhabricatorClient()
    assert client.settings.api_key == VALID_TOKEN
    assert client.base_url == "https://phab.env.example.com"


def test_conduit_uses_httpx():
    # Guard against a regression back to `requests`.
    assert client_module.httpx is httpx
