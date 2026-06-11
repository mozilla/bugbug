"""Tests for provider credential exposure/validation."""

import pytest
from hackbot_runtime.providers import AnthropicAuth, Provider, ProviderError


def test_api_key_returned_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert AnthropicAuth().api_key == "sk-test"


def test_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicAuth().api_key


def test_empty_key_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(ProviderError):
        AnthropicAuth().api_key


def test_satisfies_provider_protocol(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(AnthropicAuth(), Provider)
