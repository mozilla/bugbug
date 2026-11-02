"""Tests for W&B GCP Workload Identity Federation auth setup."""

import logging
import os

import pytest
from google.auth.exceptions import GoogleAuthError
from hackbot_runtime import wandb_wif
from hackbot_runtime.providers import ProviderError

_AUDIENCE = "https://example.wandb.example/federation"


@pytest.fixture(autouse=True)
def _reset_module_state():
    yield
    if wandb_wif._refresher is not None:
        wandb_wif._refresher.stop()
        wandb_wif._refresher = None


@pytest.fixture
def _no_federation_env(monkeypatch):
    for var in (
        wandb_wif.AUDIENCE_ENV,
        wandb_wif.TOKEN_FILE_ENV,
        wandb_wif.REFRESH_INTERVAL_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def test_is_enabled_follows_audience(monkeypatch):
    monkeypatch.delenv(wandb_wif.AUDIENCE_ENV, raising=False)
    assert wandb_wif.is_enabled() is False
    monkeypatch.setenv(wandb_wif.AUDIENCE_ENV, _AUDIENCE)
    assert wandb_wif.is_enabled() is True


def test_configure_is_inert_without_federation(_no_federation_env, monkeypatch):
    called = False

    def _should_not_fetch(*_a, **_k):
        nonlocal called
        called = True
        return "tok"

    monkeypatch.setattr(wandb_wif, "fetch_gcp_identity_token", _should_not_fetch)

    assert wandb_wif.configure() is False
    assert called is False
    assert wandb_wif.TOKEN_FILE_ENV not in os.environ


def test_configure_writes_token_and_sets_env(_no_federation_env, monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setenv(wandb_wif.AUDIENCE_ENV, _AUDIENCE)
    monkeypatch.setenv(wandb_wif.TOKEN_FILE_ENV, str(token_file))
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.setattr(
        wandb_wif, "fetch_gcp_identity_token", lambda *a, **k: "google.jwt.token"
    )

    assert wandb_wif.configure() is True

    assert token_file.read_text() == "google.jwt.token"
    assert os.environ[wandb_wif.TOKEN_FILE_ENV] == str(token_file)


def test_configure_refuses_when_api_key_set(
    _no_federation_env, monkeypatch, tmp_path, caplog
):
    monkeypatch.setenv(wandb_wif.AUDIENCE_ENV, _AUDIENCE)
    monkeypatch.setenv(wandb_wif.TOKEN_FILE_ENV, str(tmp_path / "identity-token"))
    monkeypatch.setenv("WANDB_API_KEY", "leftover")
    monkeypatch.setattr(wandb_wif, "fetch_gcp_identity_token", lambda *a, **k: "tok")

    with caplog.at_level(logging.ERROR, logger=wandb_wif.log.name):
        assert wandb_wif.configure() is False

    assert any(
        rec.levelno == logging.ERROR and "WANDB_API_KEY" in rec.message
        for rec in caplog.records
    )
    assert os.environ["WANDB_API_KEY"] == "leftover"
    assert wandb_wif._refresher is None


def test_configure_is_idempotent(_no_federation_env, monkeypatch, tmp_path):
    monkeypatch.setenv(wandb_wif.AUDIENCE_ENV, _AUDIENCE)
    monkeypatch.setenv(wandb_wif.TOKEN_FILE_ENV, str(tmp_path / "tok"))
    monkeypatch.delenv("WANDB_API_KEY", raising=False)

    calls = 0

    def _fetch(*_a, **_k):
        nonlocal calls
        calls += 1
        return "tok"

    monkeypatch.setattr(wandb_wif, "fetch_gcp_identity_token", _fetch)

    assert wandb_wif.configure() is True
    assert wandb_wif.configure() is True
    assert calls == 1


def test_configure_defaults_token_path_when_unset(_no_federation_env, monkeypatch):
    monkeypatch.setenv(wandb_wif.AUDIENCE_ENV, _AUDIENCE)
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.setattr(wandb_wif, "fetch_gcp_identity_token", lambda *a, **k: "tok")

    assert wandb_wif.configure() is True

    path = os.environ[wandb_wif.TOKEN_FILE_ENV]
    assert "wandb-wif-" in path
    assert os.path.exists(path)


def test_configure_passes_audience_to_fetch(_no_federation_env, monkeypatch, tmp_path):
    monkeypatch.setenv(wandb_wif.AUDIENCE_ENV, _AUDIENCE)
    monkeypatch.setenv(wandb_wif.TOKEN_FILE_ENV, str(tmp_path / "tok"))
    monkeypatch.delenv("WANDB_API_KEY", raising=False)

    seen = {}

    def _fetch(audience):
        seen["audience"] = audience
        return "tok"

    monkeypatch.setattr(wandb_wif, "fetch_gcp_identity_token", _fetch)

    assert wandb_wif.configure() is True
    assert seen["audience"] == _AUDIENCE


def test_fetch_uses_google_auth_with_audience(monkeypatch):
    captured = {}

    def _fake_fetch(request, audience):
        captured["request"] = request
        captured["audience"] = audience
        return "signed.jwt.value"

    monkeypatch.setattr(wandb_wif.google.oauth2.id_token, "fetch_id_token", _fake_fetch)

    token = wandb_wif.fetch_gcp_identity_token(_AUDIENCE)

    assert token == "signed.jwt.value"
    assert captured["audience"] == _AUDIENCE
    assert captured["request"] is not None


def test_fetch_wraps_google_auth_error(monkeypatch):
    def _raise(*_a, **_k):
        raise GoogleAuthError("metadata server unreachable")

    monkeypatch.setattr(wandb_wif.google.oauth2.id_token, "fetch_id_token", _raise)
    with pytest.raises(ProviderError, match="Failed to fetch a Google identity token"):
        wandb_wif.fetch_gcp_identity_token(_AUDIENCE)


def test_fetch_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        wandb_wif.google.oauth2.id_token, "fetch_id_token", lambda *a, **k: ""
    )
    with pytest.raises(ProviderError, match="empty identity token"):
        wandb_wif.fetch_gcp_identity_token(_AUDIENCE)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, wandb_wif.DEFAULT_REFRESH_INTERVAL),
        ("900", 900),
        ("not-a-number", wandb_wif.DEFAULT_REFRESH_INTERVAL),
        ("0", wandb_wif.DEFAULT_REFRESH_INTERVAL),
        ("-5", wandb_wif.DEFAULT_REFRESH_INTERVAL),
    ],
)
def test_refresh_interval_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wandb_wif.REFRESH_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(wandb_wif.REFRESH_INTERVAL_ENV, raw)
    assert wandb_wif._refresh_interval() == expected


def test_token_file_write_is_atomic_and_clean(monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setattr(
        wandb_wif, "fetch_gcp_identity_token", lambda *a, **k: "the.jwt"
    )
    refresher = wandb_wif._TokenFileRefresher(
        token_file, interval=1800, audience=_AUDIENCE
    )

    refresher._write_token()

    assert token_file.read_text() == "the.jwt"
    assert list(tmp_path.iterdir()) == [token_file]


def test_refresh_loop_survives_provider_error(monkeypatch, tmp_path):
    refresher = wandb_wif._TokenFileRefresher(
        tmp_path / "tok", interval=0, audience=_AUDIENCE
    )
    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        refresher.stop()
        raise ProviderError("transient")

    monkeypatch.setattr(wandb_wif, "fetch_gcp_identity_token", _boom)

    refresher._loop()

    assert calls == [1]
