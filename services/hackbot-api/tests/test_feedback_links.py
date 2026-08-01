"""Tests for the signed feedback token, the write-gating nonce, and anon ids."""

import uuid

import pytest
from app import feedback_links
from app.config import settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "feedback_token_secret", "test-feedback-secret")
    monkeypatch.setattr(settings, "feedback_public_base_url", "https://example.test/")
    monkeypatch.setattr(settings, "feedback_nonce_ttl_seconds", 3600)


def test_token_roundtrips():
    run_id = uuid.uuid4()
    assert feedback_links.verify_token(feedback_links.mint_token(run_id)) == run_id


def test_tampered_token_rejected():
    run_id = uuid.uuid4()
    token = feedback_links.mint_token(run_id)
    payload, _, signature = token.partition(".")

    assert feedback_links.verify_token(f"{uuid.uuid4().hex}.{signature}") is None
    assert feedback_links.verify_token(f"{payload}.{'0' * len(signature)}") is None
    assert feedback_links.verify_token(payload) is None
    assert feedback_links.verify_token("") is None
    assert feedback_links.verify_token("not-a-token.abc") is None


def test_token_does_not_verify_under_a_different_secret(monkeypatch):
    token = feedback_links.mint_token(uuid.uuid4())
    monkeypatch.setattr(settings, "feedback_token_secret", "other-secret")
    assert feedback_links.verify_token(token) is None


def test_token_signature_is_not_accepted_as_a_nonce():
    """Domain separation: the two HMACs must not be interchangeable."""
    run_id = uuid.uuid4()
    _, _, signature = feedback_links.mint_token(run_id).partition(".")
    assert feedback_links.verify_nonce(run_id, f"0.{signature}") is False


def test_feedback_url_is_absolute_and_carries_the_token():
    run_id = uuid.uuid4()
    url = feedback_links.feedback_url(run_id)
    assert url.startswith("https://example.test/feedback/")
    assert feedback_links.verify_token(url.rsplit("/", 1)[1]) == run_id


def test_nonce_roundtrips_and_is_bound_to_its_run():
    run_id = uuid.uuid4()
    nonce = feedback_links.mint_nonce(run_id)
    assert feedback_links.verify_nonce(run_id, nonce) is True
    assert feedback_links.verify_nonce(uuid.uuid4(), nonce) is False


def test_expired_nonce_rejected(monkeypatch):
    run_id = uuid.uuid4()
    nonce = feedback_links.mint_nonce(run_id)
    monkeypatch.setattr(settings, "feedback_nonce_ttl_seconds", -1)
    assert feedback_links.verify_nonce(run_id, nonce) is False


def test_malformed_nonce_rejected():
    run_id = uuid.uuid4()
    for nonce in ("", "abc", "abc.def", "12345", f"nope.{'0' * 32}"):
        assert feedback_links.verify_nonce(run_id, nonce) is False


def test_nothing_is_minted_or_accepted_without_a_secret(monkeypatch):
    run_id = uuid.uuid4()
    token = feedback_links.mint_token(run_id)
    nonce = feedback_links.mint_nonce(run_id)
    monkeypatch.setattr(settings, "feedback_token_secret", "")

    assert feedback_links.is_enabled() is False
    assert feedback_links.verify_token(token) is None
    assert feedback_links.verify_nonce(run_id, nonce) is False


def test_is_enabled_requires_a_base_url(monkeypatch):
    monkeypatch.setattr(settings, "feedback_public_base_url", "")
    assert feedback_links.is_enabled() is False


def test_anon_id_is_stable_pseudonymous_and_hides_the_ip():
    first = feedback_links.anon_id("203.0.113.7", "Firefox")
    assert first == feedback_links.anon_id("203.0.113.7", "Firefox")
    assert first != feedback_links.anon_id("203.0.113.8", "Firefox")
    assert first != feedback_links.anon_id("203.0.113.7", "Chrome")
    assert "203.0.113.7" not in first


def test_anon_id_is_none_without_any_signal():
    assert feedback_links.anon_id(None, None) is None
    assert feedback_links.anon_id("", "") is None


def test_client_ip_takes_the_first_forwarded_hop():
    assert feedback_links.client_ip_from("203.0.113.7, 70.41.3.18") == "203.0.113.7"
    assert feedback_links.client_ip_from("  203.0.113.7  ") == "203.0.113.7"
    assert feedback_links.client_ip_from(None) is None
    assert feedback_links.client_ip_from("") is None
