"""Tests for GCP Workload Identity Federation auth setup."""

import logging
import os

import pytest
from google.auth.exceptions import GoogleAuthError
from hackbot_runtime import anthropic_wif
from hackbot_runtime.providers import ProviderError


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Stop any refresher a test started and clear the module-level singleton."""
    yield
    if anthropic_wif._refresher is not None:
        anthropic_wif._refresher.stop()
        anthropic_wif._refresher = None


@pytest.fixture
def _no_federation_env(monkeypatch):
    for var in (
        anthropic_wif.RULE_ID_ENV,
        anthropic_wif.TOKEN_FILE_ENV,
        anthropic_wif.REFRESH_INTERVAL_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def test_is_enabled_follows_rule_id(monkeypatch):
    monkeypatch.delenv(anthropic_wif.RULE_ID_ENV, raising=False)
    assert anthropic_wif.is_enabled() is False
    monkeypatch.setenv(anthropic_wif.RULE_ID_ENV, "fdrl_abc")
    assert anthropic_wif.is_enabled() is True


def test_configure_is_inert_without_federation(_no_federation_env, monkeypatch):
    called = False

    def _should_not_fetch(*_a, **_k):
        nonlocal called
        called = True
        return "tok"

    monkeypatch.setattr(anthropic_wif, "fetch_gcp_identity_token", _should_not_fetch)

    assert anthropic_wif.configure() is False
    assert called is False
    assert anthropic_wif.TOKEN_FILE_ENV not in os.environ


def test_configure_writes_token_and_sets_env(_no_federation_env, monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setenv(anthropic_wif.RULE_ID_ENV, "fdrl_abc")
    monkeypatch.setenv(anthropic_wif.TOKEN_FILE_ENV, str(token_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        anthropic_wif, "fetch_gcp_identity_token", lambda *a, **k: "google.jwt.token"
    )

    assert anthropic_wif.configure() is True

    assert token_file.read_text() == "google.jwt.token"
    assert os.environ[anthropic_wif.TOKEN_FILE_ENV] == str(token_file)


def test_configure_refuses_when_api_key_set(
    _no_federation_env, monkeypatch, tmp_path, caplog
):
    monkeypatch.setenv(anthropic_wif.RULE_ID_ENV, "fdrl_abc")
    monkeypatch.setenv(anthropic_wif.TOKEN_FILE_ENV, str(tmp_path / "identity-token"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leftover")
    monkeypatch.setattr(
        anthropic_wif, "fetch_gcp_identity_token", lambda *a, **k: "tok"
    )

    with caplog.at_level(logging.ERROR, logger=anthropic_wif.log.name):
        assert anthropic_wif.configure() is False

    # A key set alongside federation is flagged as an error and left untouched.
    assert any(
        rec.levelno == logging.ERROR and "ANTHROPIC_API_KEY" in rec.message
        for rec in caplog.records
    )
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-leftover"
    assert anthropic_wif._refresher is None


def test_configure_is_idempotent(_no_federation_env, monkeypatch, tmp_path):
    monkeypatch.setenv(anthropic_wif.RULE_ID_ENV, "fdrl_abc")
    monkeypatch.setenv(anthropic_wif.TOKEN_FILE_ENV, str(tmp_path / "tok"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = 0

    def _fetch(*_a, **_k):
        nonlocal calls
        calls += 1
        return "tok"

    monkeypatch.setattr(anthropic_wif, "fetch_gcp_identity_token", _fetch)

    assert anthropic_wif.configure() is True
    assert anthropic_wif.configure() is True
    # Second call short-circuits: no new refresher, no extra fetch.
    assert calls == 1


def test_configure_defaults_token_path_when_unset(_no_federation_env, monkeypatch):
    monkeypatch.setenv(anthropic_wif.RULE_ID_ENV, "fdrl_abc")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        anthropic_wif, "fetch_gcp_identity_token", lambda *a, **k: "tok"
    )

    assert anthropic_wif.configure() is True

    path = os.environ[anthropic_wif.TOKEN_FILE_ENV]
    assert "anthropic-wif-" in path
    assert os.path.exists(path)


def test_fetch_uses_google_auth_with_audience(monkeypatch):
    captured = {}

    def _fake_fetch(request, audience):
        captured["request"] = request
        captured["audience"] = audience
        return "signed.jwt.value"

    monkeypatch.setattr(
        anthropic_wif.google.oauth2.id_token, "fetch_id_token", _fake_fetch
    )

    token = anthropic_wif.fetch_gcp_identity_token()

    assert token == "signed.jwt.value"
    assert captured["audience"] == anthropic_wif.ANTHROPIC_AUDIENCE
    assert captured["request"] is not None


def test_fetch_wraps_google_auth_error(monkeypatch):
    def _raise(*_a, **_k):
        raise GoogleAuthError("metadata server unreachable")

    monkeypatch.setattr(anthropic_wif.google.oauth2.id_token, "fetch_id_token", _raise)
    with pytest.raises(ProviderError, match="Failed to fetch a Google identity token"):
        anthropic_wif.fetch_gcp_identity_token()


def test_fetch_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        anthropic_wif.google.oauth2.id_token, "fetch_id_token", lambda *a, **k: ""
    )
    with pytest.raises(ProviderError, match="empty identity token"):
        anthropic_wif.fetch_gcp_identity_token()


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, anthropic_wif.DEFAULT_REFRESH_INTERVAL),
        ("900", 900),
        ("not-a-number", anthropic_wif.DEFAULT_REFRESH_INTERVAL),
        ("0", anthropic_wif.DEFAULT_REFRESH_INTERVAL),
        ("-5", anthropic_wif.DEFAULT_REFRESH_INTERVAL),
    ],
)
def test_refresh_interval_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(anthropic_wif.REFRESH_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(anthropic_wif.REFRESH_INTERVAL_ENV, raw)
    assert anthropic_wif._refresh_interval() == expected


def test_token_file_write_is_atomic_and_clean(monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setattr(
        anthropic_wif, "fetch_gcp_identity_token", lambda *a, **k: "the.jwt"
    )
    refresher = anthropic_wif._TokenFileRefresher(token_file, interval=1800)

    refresher._write_token()

    assert token_file.read_text() == "the.jwt"
    # No leftover temp files from the atomic replace.
    assert list(tmp_path.iterdir()) == [token_file]


def test_refresh_loop_survives_provider_error(monkeypatch, tmp_path):
    refresher = anthropic_wif._TokenFileRefresher(tmp_path / "tok", interval=0)
    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        # Stop after the first failure so the loop exits on its next tick.
        refresher.stop()
        raise ProviderError("transient")

    monkeypatch.setattr(anthropic_wif, "fetch_gcp_identity_token", _boom)

    # A ProviderError mid-run is logged and swallowed, not raised out of _loop.
    refresher._loop()

    assert calls == [1]
