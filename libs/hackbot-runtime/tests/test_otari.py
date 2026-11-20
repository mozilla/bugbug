"""Tests for routing model traffic through otari.ai."""

import os

import pytest
from hackbot_runtime import otari


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (otari.ENABLED_ENV, otari.CUSTOM_HEADERS_ENV, "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_inert_unless_enabled():
    otari.configure("r1", "bug-fix")

    assert "ANTHROPIC_BASE_URL" not in os.environ
    assert otari.CUSTOM_HEADERS_ENV not in os.environ


def test_points_the_sdk_at_otari(monkeypatch):
    monkeypatch.setenv(otari.ENABLED_ENV, "1")

    otari.configure("r1", "bug-fix")

    assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.otari.ai/api"
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
