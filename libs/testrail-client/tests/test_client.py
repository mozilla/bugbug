"""Tests for the shared TestRail client."""

import httpx
import pytest
from pydantic import ValidationError
from testrail_client import TestRailClient as Client
from testrail_client import TestRailSettings as Settings
from testrail_client import client as client_module


class _FakeResponse:
    def __init__(self, payload=None, content: bytes | None = None):
        self._payload = payload
        self.content = b"{}" if content is None else content

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


def _settings(**kwargs) -> Settings:
    defaults = {
        "username": "qa@example.com",
        "api_key": "secret",
        "project_id": 73,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _client(**kwargs) -> Client:
    return Client(_settings(**kwargs))


def _capture_request(monkeypatch, payload) -> dict:
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return _FakeResponse(payload)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


async def test_request_uses_basic_authentication(monkeypatch):
    captured = _capture_request(monkeypatch, {"id": 10})

    result = await _client(url="https://testrail.example/").request(
        "POST", "add_suite/73", {"name": "Suite"}
    )

    assert result == {"id": 10}
    assert captured["url"] == (
        "https://testrail.example/index.php?/api/v2/add_suite/73"
    )
    assert captured["auth"] == ("qa@example.com", "secret")
    assert captured["json"] == {"name": "Suite"}
    assert captured["timeout"] == 30


async def test_request_allows_empty_response(monkeypatch):
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            return _FakeResponse(content=b"")

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)

    assert await _client().request("POST", "add_suite/73") == {}


async def test_request_requires_json_object_or_array(monkeypatch):
    _capture_request(monkeypatch, "not-json-object")

    with pytest.raises(RuntimeError, match="unexpected response"):
        await _client().request("GET", "get_projects")


async def test_endpoint_wrappers(monkeypatch):
    captured = _capture_request(monkeypatch, {"id": 10})
    client = _client()

    await client.get_case_types()
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/get_case_types")

    await client.get_templates()
    assert captured["url"].endswith("/get_templates/73")

    await client.get_statuses()
    assert captured["url"].endswith("/get_statuses")

    await client.add_suite("Suite")
    assert captured["url"].endswith("/add_suite/73")
    assert captured["json"] == {"name": "Suite"}

    await client.add_section(10, "Cases")
    assert captured["url"].endswith("/add_section/73")
    assert captured["json"] == {"suite_id": 10, "name": "Cases"}

    await client.add_case(20, {"title": "Case"})
    assert captured["url"].endswith("/add_case/20")
    assert captured["json"] == {"title": "Case"}

    await client.add_run({"name": "Run", "include_all": False, "case_ids": [101]})
    assert captured["url"].endswith("/add_run/73")
    assert captured["json"] == {
        "name": "Run",
        "include_all": False,
        "case_ids": [101],
    }

    await client.add_results_for_cases(
        301, [{"case_id": 101, "status_id": 1, "comment": "ok"}]
    )
    assert captured["url"].endswith("/add_results_for_cases/301")
    assert captured["json"] == {
        "results": [{"case_id": 101, "status_id": 1, "comment": "ok"}]
    }


def test_suite_url_default_base():
    assert (
        _client().suite_url(42)
        == "https://mozilla.testrail.io/index.php?/suites/view/42"
    )


def test_suite_url_injected_base():
    assert (
        _client(url="https://testrail.example/").suite_url(42)
        == "https://testrail.example/index.php?/suites/view/42"
    )


def test_from_env_reads_environment(monkeypatch):
    monkeypatch.setenv("TESTRAIL_USERNAME", "qa@example.com")
    monkeypatch.setenv("TESTRAIL_API_KEY", "secret")
    monkeypatch.setenv("TESTRAIL_PROJECT_ID", "73")
    monkeypatch.setenv("TESTRAIL_URL", "https://testrail.env.example")

    settings = Settings.from_env()

    assert isinstance(settings, Settings)
    assert settings.username == "qa@example.com"
    assert settings.api_key == "secret"
    assert settings.project_id == 73
    assert settings.url == "https://testrail.env.example"


def test_missing_required_env_rejected(monkeypatch):
    monkeypatch.delenv("TESTRAIL_USERNAME", raising=False)
    monkeypatch.delenv("TESTRAIL_API_KEY", raising=False)
    monkeypatch.delenv("TESTRAIL_PROJECT_ID", raising=False)

    with pytest.raises(ValidationError):
        Settings.from_env()


def test_invalid_project_id_rejected():
    with pytest.raises(ValidationError):
        Settings(
            username="qa@example.com",
            api_key="secret",
            project_id="grave-yard",
        )


async def test_custom_timeout_is_passed(monkeypatch):
    captured = _capture_request(monkeypatch, {})
    await _client(timeout_seconds=5).request("GET", "get_projects")
    assert captured["timeout"] == 5


def test_defaults_from_env_when_no_settings(monkeypatch):
    monkeypatch.setenv("TESTRAIL_USERNAME", "qa@example.com")
    monkeypatch.setenv("TESTRAIL_API_KEY", "secret")
    monkeypatch.setenv("TESTRAIL_PROJECT_ID", "73")

    client = Client()

    assert client.settings.username == "qa@example.com"
    assert client.settings.project_id == 73


def test_client_uses_httpx():
    assert client_module.httpx is httpx
