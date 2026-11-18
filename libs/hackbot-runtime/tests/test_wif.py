"""Tests for GCP Workload Identity Federation auth setup."""

import logging
import os

import pytest
from google.auth.exceptions import GoogleAuthError
from hackbot_runtime import wif
from hackbot_runtime.providers import ProviderError

_WANDB_AUDIENCE = "https://example.wandb.example/federation"

# The value that enables federation for each provider, as the deploy would set it.
_ENABLE_VALUES = {
    wif.AnthropicWifSettings.provider_id: "fdrl_abc",
    wif.OpenAIWifSettings.provider_id: "idpm_abc",
    wif.WandbWifSettings.provider_id: _WANDB_AUDIENCE,
}

_EXPECTED_AUDIENCES = {
    wif.AnthropicWifSettings.provider_id: "https://api.anthropic.com",
    wif.OpenAIWifSettings.provider_id: "https://api.openai.com/v1",
    wif.WandbWifSettings.provider_id: _WANDB_AUDIENCE,
}

all_configs = pytest.mark.parametrize(
    "config", wif.PROVIDERS, ids=[c.provider_id for c in wif.PROVIDERS]
)


_ENABLE_ENVS = {
    wif.AnthropicWifSettings.provider_id: "ANTHROPIC_FEDERATION_RULE_ID",
    wif.OpenAIWifSettings.provider_id: "OPENAI_FEDERATION_RULE_ID",
    wif.WandbWifSettings.provider_id: "WANDB_FEDERATION_AUDIENCE",
}

_API_KEY_ENVS = {
    wif.AnthropicWifSettings.provider_id: "ANTHROPIC_API_KEY",
    wif.OpenAIWifSettings.provider_id: "OPENAI_API_KEY",
    wif.WandbWifSettings.provider_id: "WANDB_API_KEY",
}


@pytest.fixture(autouse=True)
def _no_federation_env(monkeypatch):
    for prefix in ("ANTHROPIC_", "OPENAI_", "WANDB_"):
        for var in list(os.environ):
            if var.startswith(prefix):
                monkeypatch.delenv(var)


def _enable(monkeypatch, config):
    monkeypatch.setenv(
        _ENABLE_ENVS[config.provider_id], _ENABLE_VALUES[config.provider_id]
    )


@all_configs
def test_enabled_follows_enabled_env(monkeypatch, config):
    assert config.from_env().enabled is False
    _enable(monkeypatch, config)
    assert config.from_env().enabled is True


@all_configs
def test_empty_enabled_env_counts_as_unset(monkeypatch, config):
    monkeypatch.setenv(_ENABLE_ENVS[config.provider_id], "")
    assert config.from_env().enabled is False


@all_configs
def test_configure_is_inert_without_federation(monkeypatch, config):
    called = False

    def _should_not_fetch(*_a, **_k):
        nonlocal called
        called = True
        return "tok"

    monkeypatch.setattr(wif, "fetch_gcp_identity_token", _should_not_fetch)

    with wif.configure(config.from_env()):
        pass
    assert called is False
    assert config.token_file_env not in os.environ


@all_configs
def test_configure_writes_token_to_configured_path(monkeypatch, tmp_path, config):
    token_file = tmp_path / "identity-token"
    _enable(monkeypatch, config)
    monkeypatch.setenv(config.token_file_env, str(token_file))
    seen = {}

    def _fetch(audience):
        seen["audience"] = audience
        return "google.jwt.token"

    monkeypatch.setattr(wif, "fetch_gcp_identity_token", _fetch)

    with wif.configure(config.from_env()):
        pass

    assert token_file.read_text() == "google.jwt.token"
    assert seen["audience"] == _EXPECTED_AUDIENCES[config.provider_id]


def test_configure_tracks_providers_independently(monkeypatch, tmp_path):
    for config in wif.PROVIDERS:
        _enable(monkeypatch, config)
        monkeypatch.setenv(config.token_file_env, str(tmp_path / config.provider_id))
    monkeypatch.setattr(wif, "fetch_gcp_identity_token", lambda aud: f"tok:{aud}")

    for config in wif.PROVIDERS:
        with wif.configure(config.from_env()):
            pass

    for config in wif.PROVIDERS:
        expected = f"tok:{_EXPECTED_AUDIENCES[config.provider_id]}"
        assert (tmp_path / config.provider_id).read_text() == expected


@all_configs
def test_configure_defaults_token_path_when_unset(monkeypatch, caplog, config):
    _enable(monkeypatch, config)
    monkeypatch.setattr(wif, "fetch_gcp_identity_token", lambda *a, **k: "tok")

    with (
        caplog.at_level(logging.INFO, logger=wif.log.name),
        wif.configure(config.from_env()),
    ):
        pass

    path = os.environ[config.token_file_env]
    assert f"{config.provider_id}-wif-" in path
    assert os.path.isabs(path)
    assert os.path.exists(path)
    assert any(
        rec.levelno == logging.INFO
        and config.token_file_env in rec.message
        and "temporary" in rec.message
        for rec in caplog.records
    )


@pytest.mark.parametrize(
    "config",
    [wif.AnthropicWifSettings, wif.WandbWifSettings],
    ids=["anthropic", "wandb"],
)
def test_configure_refuses_when_shadowing_api_key_set(
    monkeypatch, tmp_path, caplog, config
):
    _enable(monkeypatch, config)
    monkeypatch.setenv(config.token_file_env, str(tmp_path / "identity-token"))
    monkeypatch.setenv(_API_KEY_ENVS[config.provider_id], "sk-leftover")
    monkeypatch.setattr(wif, "fetch_gcp_identity_token", lambda *a, **k: "tok")

    with (
        caplog.at_level(logging.ERROR, logger=wif.log.name),
        wif.configure(config.from_env()),
    ):
        pass

    # A key set alongside federation is flagged as an error and left untouched.
    assert any(
        rec.levelno == logging.ERROR and config.name in rec.message
        for rec in caplog.records
    )
    assert os.environ[_API_KEY_ENVS[config.provider_id]] == "sk-leftover"


def test_openai_configure_warns_but_proceeds_with_api_key(
    monkeypatch, tmp_path, caplog
):
    _enable(monkeypatch, wif.OpenAIWifSettings)
    monkeypatch.setenv(
        wif.OpenAIWifSettings.token_file_env,
        str(tmp_path / "identity-token"),
    )
    monkeypatch.setenv(_API_KEY_ENVS[wif.OpenAIWifSettings.provider_id], "sk-leftover")
    monkeypatch.setattr(wif, "fetch_gcp_identity_token", lambda *a, **k: "tok")

    with (
        caplog.at_level(logging.WARNING, logger=wif.log.name),
        wif.configure(wif.OpenAIWifSettings.from_env()),
    ):
        pass

    # Codex prefers federation over the key, so the key is only a warning.
    assert any(
        rec.levelno == logging.WARNING and "OpenAI API key" in rec.message
        for rec in caplog.records
    )


def test_openai_audience_override(monkeypatch, tmp_path):
    _enable(monkeypatch, wif.OpenAIWifSettings)
    monkeypatch.setenv("OPENAI_WIF_AUDIENCE", "https://custom.example/aud")
    monkeypatch.setenv(wif.OpenAIWifSettings.token_file_env, str(tmp_path / "tok"))
    seen = {}

    def _fetch(audience):
        seen["audience"] = audience
        return "tok"

    monkeypatch.setattr(wif, "fetch_gcp_identity_token", _fetch)

    with wif.configure(wif.OpenAIWifSettings.from_env()):
        pass
    assert seen["audience"] == "https://custom.example/aud"


def test_fetch_uses_google_auth_with_audience(monkeypatch):
    captured = {}

    def _fake_fetch(request, audience):
        captured["request"] = request
        captured["audience"] = audience
        return "signed.jwt.value"

    monkeypatch.setattr(wif.google.oauth2.id_token, "fetch_id_token", _fake_fetch)

    token = wif.fetch_gcp_identity_token("https://aud.example")

    assert token == "signed.jwt.value"
    assert captured["audience"] == "https://aud.example"
    assert captured["request"] is not None


def test_fetch_wraps_google_auth_error(monkeypatch):
    def _raise(*_a, **_k):
        raise GoogleAuthError("metadata server unreachable")

    monkeypatch.setattr(wif.google.oauth2.id_token, "fetch_id_token", _raise)
    with pytest.raises(ProviderError, match="Failed to fetch a Google identity token"):
        wif.fetch_gcp_identity_token("https://aud.example")


def test_fetch_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        wif.google.oauth2.id_token, "fetch_id_token", lambda *a, **k: ""
    )
    with pytest.raises(ProviderError, match="empty identity token"):
        wif.fetch_gcp_identity_token("https://aud.example")


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, wif.DEFAULT_REFRESH_INTERVAL),
        ("900", 900),
    ],
)
def test_refresh_interval_parsing(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("ANTHROPIC_IDENTITY_TOKEN_REFRESH_SECONDS", raw)
    settings = wif.AnthropicWifSettings.from_env()
    assert settings.identity_token_refresh_seconds == expected


def test_token_file_write_is_atomic_and_clean(monkeypatch, tmp_path):
    token_file = tmp_path / "identity-token"
    monkeypatch.setattr(wif, "fetch_gcp_identity_token", lambda *a, **k: "the.jwt")
    refresher = wif.TokenFileRefresher(
        token_file, interval=1800, audience="aud", provider_id="test", name="Test"
    )

    refresher._write_token()

    assert token_file.read_text() == "the.jwt"
    assert list(tmp_path.iterdir()) == [token_file]


def test_refresh_loop_survives_provider_error(monkeypatch, tmp_path):
    refresher = wif.TokenFileRefresher(
        tmp_path / "tok", interval=0, audience="aud", provider_id="test", name="Test"
    )
    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        refresher.stop()
        raise ProviderError("transient")

    monkeypatch.setattr(wif, "fetch_gcp_identity_token", _boom)

    refresher._loop()

    assert calls == [1]


def test_wandb_refresh_forces_a_new_exchange(monkeypatch, tmp_path):
    from weave.wandb_interface import auth

    token_file = tmp_path / "identity-token"
    credentials_file = tmp_path / "credentials.json"
    monkeypatch.setenv(wif.WandbWifSettings.token_file_env, str(token_file))
    monkeypatch.setenv(auth.CREDENTIALS_FILE_ENV, str(credentials_file))
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    credentials = auth.get_wandb_credentials()
    credentials._cached_access_token = ("stale", None)
    credentials_file.write_text("{}")

    config = wif.WandbWifSettings(audience=_WANDB_AUDIENCE)
    refresher = wif.TokenFileRefresher(
        token_file,
        interval=0,
        audience=_WANDB_AUDIENCE,
        provider_id=config.provider_id,
        name=config.name,
        on_refresh=config.on_token_refresh,
    )

    def _fetch(*_a, **_k):
        refresher.stop()
        return "fresh.jwt"

    monkeypatch.setattr(wif, "fetch_gcp_identity_token", _fetch)

    refresher._loop()

    assert credentials._cached_access_token is None
    assert not credentials_file.exists()
