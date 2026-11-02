"""Tests for OpenAI GCP Workload Identity Federation auth setup."""

import os

import pytest
from google.auth.exceptions import GoogleAuthError
from hackbot_runtime import openai_wif
from hackbot_runtime.providers import ProviderError


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Stop any refresher a test started and clear the module-level singleton."""
    yield
    if openai_wif._refresher is not None:
        openai_wif._refresher.stop()
        openai_wif._refresher = None


@pytest.fixture
def _no_federation_env(monkeypatch):
    for var in (
        openai_wif.RULE_ID_ENV,
        openai_wif.AUDIENCE_ENV,
        openai_wif.TOKEN_FILE_ENV,
        openai_wif.REFRESH_INTERVAL_ENV,
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_is_enabled_follows_rule_id(monkeypatch):
    monkeypatch.delenv(openai_wif.RULE_ID_ENV, raising=False)
    assert openai_wif.is_enabled() is False
    monkeypatch.setenv(openai_wif.RULE_ID_ENV, "idpm_abc")
    assert openai_wif.is_enabled() is True


def test_configure_is_inert_without_federation(_no_federation_env, monkeypatch):
    called = False

    def _should_not_fetch(*_a, **_k):
        nonlocal called
        called = True
        return "tok"

    monkeypatch.setattr(openai_wif, "fetch_gcp_identity_token", _should_not_fetch)

    assert openai_wif.configure() is False
    assert called is False
    assert openai_wif.TOKEN_FILE_ENV not in os.environ


def test_configure_writes_token_and_sets_env(_no_federation_env, monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setenv(openai_wif.RULE_ID_ENV, "idpm_abc")
    monkeypatch.setenv(openai_wif.TOKEN_FILE_ENV, str(token_file))
    captured = {}

    def _fetch(audience):
        captured["audience"] = audience
        return "google.jwt.token"

    monkeypatch.setattr(openai_wif, "fetch_gcp_identity_token", _fetch)

    assert openai_wif.configure() is True

    assert token_file.read_text() == "google.jwt.token"
    assert os.environ[openai_wif.TOKEN_FILE_ENV] == str(token_file)
    assert captured["audience"] == openai_wif.OPENAI_AUDIENCE


def test_configure_is_idempotent(_no_federation_env, monkeypatch, tmp_path):
    monkeypatch.setenv(openai_wif.RULE_ID_ENV, "idpm_abc")
    monkeypatch.setenv(openai_wif.TOKEN_FILE_ENV, str(tmp_path / "tok"))

    calls = 0

    def _fetch(*_a, **_k):
        nonlocal calls
        calls += 1
        return "tok"

    monkeypatch.setattr(openai_wif, "fetch_gcp_identity_token", _fetch)

    assert openai_wif.configure() is True
    assert openai_wif.configure() is True
    # Second call short-circuits: no new refresher, no extra fetch.
    assert calls == 1


def test_configure_defaults_token_path_when_unset(_no_federation_env, monkeypatch):
    monkeypatch.setenv(openai_wif.RULE_ID_ENV, "idpm_abc")
    monkeypatch.setattr(openai_wif, "fetch_gcp_identity_token", lambda *a, **k: "tok")

    assert openai_wif.configure() is True

    path = os.environ[openai_wif.TOKEN_FILE_ENV]
    assert "openai-wif-" in path
    assert os.path.isabs(path)
    assert os.path.exists(path)


def test_fetch_uses_google_auth_with_audience(monkeypatch):
    captured = {}

    def _fake_fetch(request, audience):
        captured["request"] = request
        captured["audience"] = audience
        return "signed.jwt.value"

    monkeypatch.setattr(
        openai_wif.google.oauth2.id_token, "fetch_id_token", _fake_fetch
    )

    token = openai_wif.fetch_gcp_identity_token()

    assert token == "signed.jwt.value"
    assert captured["audience"] == openai_wif.OPENAI_AUDIENCE
    assert captured["request"] is not None


def test_fetch_wraps_google_auth_error(monkeypatch):
    def _raise(*_a, **_k):
        raise GoogleAuthError("metadata server unreachable")

    monkeypatch.setattr(openai_wif.google.oauth2.id_token, "fetch_id_token", _raise)
    with pytest.raises(ProviderError, match="Failed to fetch a Google identity token"):
        openai_wif.fetch_gcp_identity_token()


def test_fetch_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        openai_wif.google.oauth2.id_token, "fetch_id_token", lambda *a, **k: ""
    )
    with pytest.raises(ProviderError, match="empty identity token"):
        openai_wif.fetch_gcp_identity_token()


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, openai_wif.DEFAULT_REFRESH_INTERVAL),
        ("900", 900),
        ("not-a-number", openai_wif.DEFAULT_REFRESH_INTERVAL),
        ("0", openai_wif.DEFAULT_REFRESH_INTERVAL),
        ("-5", openai_wif.DEFAULT_REFRESH_INTERVAL),
    ],
)
def test_refresh_interval_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(openai_wif.REFRESH_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(openai_wif.REFRESH_INTERVAL_ENV, raw)
    assert openai_wif._refresh_interval() == expected


def test_token_file_write_is_atomic_and_clean(monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setattr(
        openai_wif, "fetch_gcp_identity_token", lambda *a, **k: "the.jwt"
    )
    refresher = openai_wif._TokenFileRefresher(token_file, interval=1800)

    refresher._write_token()

    assert token_file.read_text() == "the.jwt"
    # No leftover temp files from the atomic replace.
    assert list(tmp_path.iterdir()) == [token_file]


def test_refresh_loop_survives_provider_error(monkeypatch, tmp_path):
    refresher = openai_wif._TokenFileRefresher(tmp_path / "tok", interval=0)
    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        # Stop after the first failure so the loop exits on its next tick.
        refresher.stop()
        raise ProviderError("transient")

    monkeypatch.setattr(openai_wif, "fetch_gcp_identity_token", _boom)

    # A ProviderError mid-run is logged and swallowed, not raised out of _loop.
    refresher._loop()

    assert calls == [1]
