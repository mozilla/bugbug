"""Tests for routing model traffic through otari.ai."""

import os
import sys
import tomllib
from pathlib import Path

import httpx
import pytest
from hackbot_runtime import otari


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        otari.ENABLED_ENV,
        otari.BASE_URL_ENV,
        otari.CUSTOM_HEADERS_ENV,
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUDIENCE",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
        "OPENAI_FEDERATION_RULE_ID",
        "CODEX_HOME",
    ):
        monkeypatch.delenv(var, raising=False)


def _codex_config() -> dict:
    return tomllib.loads((Path(os.environ["CODEX_HOME"]) / "config.toml").read_text())


def test_inert_unless_enabled():
    otari.configure("r1", "bug-fix")

    assert "ANTHROPIC_BASE_URL" not in os.environ
    assert otari.CUSTOM_HEADERS_ENV not in os.environ
    assert "ANTHROPIC_AUDIENCE" not in os.environ
    assert "CODEX_HOME" not in os.environ


def test_points_the_sdk_at_otari(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")

    otari.configure("r1", "bug-fix")

    assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.otari.ai/api"
    assert os.environ["ANTHROPIC_AUDIENCE"] == "https://api.otari.ai"
    assert os.environ[otari.CUSTOM_HEADERS_ENV].splitlines() == [
        "Otari-Conversation-Id: hackbot-r1",
        "Otari-Tags: app=hackbot,agent=bug-fix",
        "Otari-Web-Search: auto",
    ]


def test_keeps_existing_custom_headers(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")
    monkeypatch.setenv(otari.CUSTOM_HEADERS_ENV, "X-Existing: 1\n")

    otari.configure("r1", "bug-fix")

    assert os.environ[otari.CUSTOM_HEADERS_ENV].splitlines()[0] == "X-Existing: 1"


def test_keeps_an_audience_the_deploy_set(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")
    monkeypatch.setenv("ANTHROPIC_AUDIENCE", "https://eu.api.otari.ai")

    otari.configure("r1", "bug-fix")

    assert os.environ["ANTHROPIC_AUDIENCE"] == "https://eu.api.otari.ai"


def test_points_codex_at_otari(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")

    otari.configure("r1", "bug-fix")

    config = _codex_config()
    assert config["model_provider"] == "otari"
    provider = config["model_providers"]["otari"]
    assert provider["base_url"] == "https://api.otari.ai/api/v1"
    assert provider["wire_api"] == "responses"
    assert provider["http_headers"] == {
        "Otari-Conversation-Id": "hackbot-r1",
        "Otari-Tags": "app=hackbot,agent=bug-fix",
        "Otari-Web-Search": "auto",
    }
    assert provider["auth"]["command"] == sys.executable
    assert provider["auth"]["args"] == ["-m", "hackbot_runtime.otari"]


def test_drops_openai_federation(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")
    monkeypatch.setenv("OPENAI_FEDERATION_RULE_ID", "idpm_abc")

    otari.configure("r1", "bug-fix")

    assert "OPENAI_FEDERATION_RULE_ID" not in os.environ


def test_base_url_override(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")
    monkeypatch.setenv(otari.BASE_URL_ENV, "https://eu.api.otari.ai/api")

    otari.configure("r1", "bug-fix")

    assert os.environ["ANTHROPIC_BASE_URL"] == "https://eu.api.otari.ai/api"
    provider = _codex_config()["model_providers"]["otari"]
    assert provider["base_url"] == "https://eu.api.otari.ai/api/v1"


def test_access_token_is_the_api_key_without_federation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tk-local")

    assert otari.access_token() == "tk-local"


def test_access_token_needs_a_credential():
    with pytest.raises(RuntimeError, match="ANTHROPIC_FEDERATION_RULE_ID"):
        otari.access_token()


def _federation_env(monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    token_file.write_text("google.jwt.token\n")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.otari.ai/api")
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "rule-1")
    monkeypatch.setenv("ANTHROPIC_IDENTITY_TOKEN_FILE", str(token_file))


def test_access_token_exchanges_the_identity_token(monkeypatch, tmp_path):
    _federation_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_ORGANIZATION_ID", "org-1")
    sent = {}

    def _post(url, json, timeout):
        sent.update(url=url, json=json)
        return httpx.Response(
            200,
            json={"access_token": "at-1", "expires_in": 600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(otari.httpx, "post", _post)

    assert otari.access_token() == "at-1"
    assert sent == {
        "url": "https://api.otari.ai/api/v1/oauth/token",
        "json": {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "google.jwt.token",
            "federation_rule_id": "rule-1",
            "organization_id": "org-1",
        },
    }


def test_access_token_reports_a_refused_exchange(monkeypatch, tmp_path):
    _federation_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        otari.httpx,
        "post",
        lambda url, json, timeout: httpx.Response(
            401, json={"error": "invalid_grant"}, request=httpx.Request("POST", url)
        ),
    )

    with pytest.raises(RuntimeError, match="401"):
        otari.access_token()
