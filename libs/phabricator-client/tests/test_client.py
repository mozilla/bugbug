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
    """Stub httpx.post to return `payload`; return a dict capturing the call."""
    captured: dict = {}

    def _post(url, data=None, timeout=None):
        captured["url"] = url
        captured["params"] = json.loads(data["params"])
        captured["timeout"] = timeout
        return _FakeResponse(payload)

    monkeypatch.setattr(client_module.httpx, "post", _post)
    return captured


def test_conduit_request_returns_result(monkeypatch):
    captured = _capture_post(monkeypatch, {"result": {"data": [1, 2]}})
    result = _client().conduit_request("some.method", foo="bar")
    assert result == {"data": [1, 2]}
    # Token is injected into the request body, not a header.
    assert captured["params"]["__conduit__"] == {"token": VALID_TOKEN}
    assert captured["params"]["foo"] == "bar"
    assert captured["url"].endswith("/api/some.method")
    assert captured["timeout"] == 60


def test_conduit_request_raises_on_error_code(monkeypatch):
    _capture_post(monkeypatch, {"error_code": "ERR-CONDUIT", "error_info": "nope"})
    with pytest.raises(RuntimeError, match="ERR-CONDUIT"):
        _client().conduit_request("some.method")


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


def test_search_transactions(monkeypatch):
    _capture_post(monkeypatch, {"result": {"data": [{"phid": "PHID-XACT-1"}]}})
    assert _client().search_transactions("PHID-DREV-1") == [{"phid": "PHID-XACT-1"}]


def test_search_revision_found(monkeypatch):
    _capture_post(monkeypatch, {"result": {"data": [{"id": 42}]}})
    assert _client().search_revision("PHID-DREV-1") == {"id": 42}


def test_search_revision_missing(monkeypatch):
    _capture_post(monkeypatch, {"result": {"data": []}})
    assert _client().search_revision("PHID-DREV-1") is None


def test_revision_url_default_base():
    assert _client().revision_url(42) == "https://phabricator.services.mozilla.com/D42"


def test_revision_url_injected_base():
    client = _client(url="https://phab.example.com/")
    assert client.revision_url(7) == "https://phab.example.com/D7"


def test_custom_timeout_is_passed(monkeypatch):
    captured = _capture_post(monkeypatch, {"result": {}})
    _client(timeout_seconds=5).conduit_request("some.method")
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
