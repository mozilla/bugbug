"""Tests for the Phabricator webhook receiver.

Covers HMAC signature verification, mention detection / loop prevention, the
revision -> (revision_id, bug_id) resolution, and the route's ignore/trigger
branches (test ping, non-DREV, dedupe, and a successful @hackbot mention).
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from app.auth import verify_phabricator_signature
from app.config import settings
from app.main import app
from app.phabricator_webhook import (
    _join_comments,
    find_hackbot_mentions,
    resolve_revision,
    triggering_transaction_phids,
)
from app.routers import webhooks
from fastapi.testclient import TestClient

SECRET = "test-secret"


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# --- signature verification ---


def test_signature_valid(monkeypatch):
    monkeypatch.setattr(settings.webhook, "secret", SECRET)
    body = b'{"a": 1}'
    assert verify_phabricator_signature(body, _sign(body)) is True


def test_signature_invalid(monkeypatch):
    monkeypatch.setattr(settings.webhook, "secret", SECRET)
    assert verify_phabricator_signature(b"body", "deadbeef") is False


def test_signature_missing_header(monkeypatch):
    monkeypatch.setattr(settings.webhook, "secret", SECRET)
    assert verify_phabricator_signature(b"body", None) is False


def test_signature_unconfigured_secret(monkeypatch):
    monkeypatch.setattr(settings.webhook, "secret", "")
    assert verify_phabricator_signature(b"body", _sign(b"body")) is False


# --- mention detection / loop prevention ---


def _comment_txn(phid: str, author: str, raw: str, txn_type: str = "comment") -> dict:
    return {
        "phid": phid,
        "type": txn_type,
        "authorPHID": author,
        "comments": [{"content": {"raw": raw}}],
    }


def test_find_mention_matches():
    txns = [_comment_txn("PHID-XACT-1", "PHID-USER-a", "hey @hackbot please fix")]
    assert find_hackbot_mentions(
        txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
    ) == ["hey @hackbot please fix"]


def test_find_mention_no_token():
    txns = [_comment_txn("PHID-XACT-1", "PHID-USER-a", "just a normal comment")]
    assert (
        find_hackbot_mentions(
            txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
        )
        == []
    )


def test_find_mention_ignores_bot_author():
    # The bot's own @hackbot comment must not re-trigger a run.
    txns = [_comment_txn("PHID-XACT-1", "PHID-USER-bot", "@hackbot did the thing")]
    assert (
        find_hackbot_mentions(
            txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
        )
        == []
    )


def test_find_mention_ignores_non_triggering_transaction():
    txns = [_comment_txn("PHID-XACT-OLD", "PHID-USER-a", "@hackbot fix")]
    assert (
        find_hackbot_mentions(
            txns, {"PHID-XACT-NEW"}, bot_phid="PHID-USER-bot", token="@hackbot"
        )
        == []
    )


def test_find_mention_ignores_non_comment_type():
    txns = [_comment_txn("PHID-XACT-1", "PHID-USER-a", "@hackbot", txn_type="status")]
    assert (
        find_hackbot_mentions(
            txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
        )
        == []
    )


def test_find_mention_matches_inline_comment():
    txns = [
        _comment_txn("PHID-XACT-1", "PHID-USER-a", "@hackbot here", txn_type="inline")
    ]
    assert find_hackbot_mentions(
        txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
    ) == ["@hackbot here"]


def test_find_mention_collects_all_inline_matches():
    # A review with several inline @hackbot comments (each its own transaction)
    # yields all of them, in order; comments without the token are skipped.
    txns = [
        _comment_txn("PHID-XACT-1", "PHID-USER-a", "@hackbot fix this", "inline"),
        _comment_txn("PHID-XACT-2", "PHID-USER-a", "no mention here", "inline"),
        _comment_txn("PHID-XACT-3", "PHID-USER-a", "@hackbot and this too", "inline"),
    ]
    assert find_hackbot_mentions(
        txns,
        {"PHID-XACT-1", "PHID-XACT-2", "PHID-XACT-3"},
        bot_phid="PHID-USER-bot",
        token="@hackbot",
    ) == ["@hackbot fix this", "@hackbot and this too"]


def test_find_mention_one_per_transaction_ignores_comment_versions():
    # A transaction's `comments` list is version history, not distinct comments;
    # only one match is taken per transaction.
    txn = {
        "phid": "PHID-XACT-1",
        "type": "inline",
        "authorPHID": "PHID-USER-a",
        "comments": [
            {"content": {"raw": "@hackbot v1"}},
            {"content": {"raw": "@hackbot v2 edited"}},
        ],
    }
    assert find_hackbot_mentions(
        [txn], {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
    ) == ["@hackbot v1"]


def test_join_comments_single_passthrough():
    assert _join_comments(["only one"]) == "only one"


def test_join_comments_numbers_multiple():
    joined = _join_comments(["first", "second"])
    assert "[comment 1]\nfirst" in joined
    assert "[comment 2]\nsecond" in joined


# --- revision resolution ---


class _FakeClient:
    def __init__(self, revision):
        self._revision = revision

    async def search_revision(self, phid):
        return self._revision


async def test_resolve_revision_with_bug():
    client = _FakeClient({"id": 42, "fields": {"bugzilla.bug-id": "12345"}})
    assert await resolve_revision(client, "PHID-DREV-x") == (42, 12345)


async def test_resolve_revision_no_bug():
    client = _FakeClient({"id": 42, "fields": {"bugzilla.bug-id": ""}})
    assert await resolve_revision(client, "PHID-DREV-x") == (42, None)


async def test_resolve_revision_not_found():
    client = _FakeClient(None)
    assert await resolve_revision(client, "PHID-DREV-x") == (None, None)


# --- payload parsing ---


def test_triggering_transaction_phids():
    payload = {"transactions": [{"phid": "A"}, {"phid": "B"}, {"nophid": True}]}
    assert triggering_transaction_phids(payload) == ["A", "B"]


# --- route ---


class _FakeHackbotClient:
    """Stub for HackbotClient, injected via dependency_overrides."""

    def __init__(self):
        self.calls = []

    async def trigger_run(self, agent_name, inputs):
        self.calls.append((agent_name, inputs))
        return "run-abc"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings.webhook, "secret", SECRET)
    # Fresh dedupe cache per test.
    webhooks._seen_transactions.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _post(client, payload: dict):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/phabricator",
        content=body,
        headers={"X-Phabricator-Webhook-Signature": _sign(body)},
    )


def test_route_rejects_bad_signature(client):
    body = json.dumps({"object": {"type": "DREV"}}).encode()
    resp = client.post(
        "/webhooks/phabricator",
        content=body,
        headers={"X-Phabricator-Webhook-Signature": "wrong"},
    )
    assert resp.status_code == 401


def test_route_ignores_test_ping(client):
    resp = _post(client, {"action": {"test": True}, "object": {"type": "DREV"}})
    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored"


def test_route_ignores_non_drev(client):
    resp = _post(client, {"object": {"type": "TASK", "phid": "PHID-TASK-1"}})
    assert resp.status_code == 202
    assert resp.json()["reason"] == "not a revision"


def test_route_ignores_no_mention(client, monkeypatch):
    monkeypatch.setattr(
        webhooks, "detect_mention_and_revision", AsyncMock(return_value=None)
    )
    resp = _post(
        client,
        {
            "object": {"type": "DREV", "phid": "PHID-DREV-1"},
            "transactions": [{"phid": "PHID-XACT-1"}],
        },
    )
    assert resp.status_code == 202
    assert resp.json()["reason"] == "no actionable @hackbot mention"


def test_route_triggers_run(client, monkeypatch):
    monkeypatch.setattr(
        webhooks,
        "detect_mention_and_revision",
        AsyncMock(return_value=("@hackbot please fix", 42, 12345)),
    )
    fake_api = _FakeHackbotClient()
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: fake_api

    resp = _post(
        client,
        {
            "object": {"type": "DREV", "phid": "PHID-DREV-1"},
            "transactions": [{"phid": "PHID-XACT-1"}],
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "triggered", "run_id": "run-abc"}
    assert fake_api.calls == [
        (
            "bug-fix",
            {
                "bug_id": 12345,
                "revision_id": 42,
                "comment": "@hackbot please fix",
            },
        )
    ]


def test_route_dedupes_retried_delivery(client, monkeypatch):
    detect = AsyncMock(return_value=("@hackbot please fix", 42, 12345))
    monkeypatch.setattr(webhooks, "detect_mention_and_revision", detect)
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: _FakeHackbotClient()

    payload = {
        "object": {"type": "DREV", "phid": "PHID-DREV-1"},
        "transactions": [{"phid": "PHID-XACT-1"}],
    }
    first = _post(client, payload)
    second = _post(client, payload)

    assert first.json()["status"] == "triggered"
    assert second.json()["reason"] == "duplicate delivery"
    assert detect.call_count == 1
