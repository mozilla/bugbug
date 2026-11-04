"""Tests for routing model traffic through an Otari gateway."""

import os

import pytest
from hackbot_runtime import anthropic_wif, otari


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        otari.BASE_URL_ENV,
        otari.CUSTOM_HEADERS_ENV,
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_inert_without_base_url():
    assert otari.configure("r1", "bug-fix") is False
    assert "ANTHROPIC_BASE_URL" not in os.environ
    assert otari.CUSTOM_HEADERS_ENV not in os.environ


def test_points_the_sdk_at_the_gateway(monkeypatch):
    monkeypatch.setenv(otari.BASE_URL_ENV, "https://otari.example/api/v1/")

    assert otari.configure("r1", "bug-fix") is True

    assert os.environ["ANTHROPIC_BASE_URL"] == "https://otari.example/api"
    assert os.environ[otari.CUSTOM_HEADERS_ENV].splitlines() == [
        "Otari-Conversation-Id: hackbot-r1",
        "Otari-Tags: app=hackbot,agent=bug-fix",
        "Otari-Web-Search: auto",
    ]


def test_leaves_authentication_to_the_runtime(monkeypatch):
    """Federation and a local key both pass through untouched."""
    monkeypatch.setenv(otari.BASE_URL_ENV, "https://otari.example/api/v1")
    monkeypatch.setenv(anthropic_wif.RULE_ID_ENV, "rule-1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tk-local")

    otari.configure("r1", "bug-fix")

    assert os.environ[anthropic_wif.RULE_ID_ENV] == "rule-1"
    assert os.environ["ANTHROPIC_API_KEY"] == "tk-local"


def test_keeps_existing_custom_headers(monkeypatch):
    monkeypatch.setenv(otari.BASE_URL_ENV, "https://otari.example/api/v1")
    monkeypatch.setenv(otari.CUSTOM_HEADERS_ENV, "X-Existing: 1\n")

    otari.configure("r1", "bug-fix")

    assert os.environ[otari.CUSTOM_HEADERS_ENV].splitlines()[0] == "X-Existing: 1"
