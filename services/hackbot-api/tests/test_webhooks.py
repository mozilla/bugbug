"""Tests for the Phabricator and Bugzilla webhook receivers.

Covers HMAC signature verification, mention detection / loop prevention, the
revision -> (revision_id, bug_id) resolution, and the route's ignore/trigger
branches. Bugzilla coverage includes shared-secret auth, structured needinfo
detection, self/private-event suppression, dedupe, and dispatch retry behavior.
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from app.auth import (
    verify_bugzilla_webhook_secret,
    verify_phabricator_signature,
)
from app.bugzilla_webhook import detect_needinfo_request
from app.config import settings
from app.main import app
from app.phabricator_authorization import (
    AUTHORIZED_GROUP_PHID,
    PhabricatorAuthorizer,
)
from app.phabricator_webhook import (
    HackbotMention,
    _format_comment,
    detect_mention_and_revision,
    find_hackbot_mentions,
    resolve_revision,
    triggering_transaction_phids,
)
from app.routers import webhooks
from fastapi.testclient import TestClient

SECRET = "test-secret"
BUGZILLA_SECRET = "test-bugzilla-secret"
BUGZILLA_BOT_LOGIN = "hackbot@mozilla.tld"


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


def test_bugzilla_secret_valid(monkeypatch):
    monkeypatch.setattr(settings.bugzilla_webhook, "secret", BUGZILLA_SECRET)
    assert verify_bugzilla_webhook_secret(BUGZILLA_SECRET) is True


def test_bugzilla_secret_invalid_or_missing(monkeypatch):
    monkeypatch.setattr(settings.bugzilla_webhook, "secret", BUGZILLA_SECRET)
    assert verify_bugzilla_webhook_secret("wrong") is False
    assert verify_bugzilla_webhook_secret(None) is False


def test_bugzilla_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(settings.bugzilla_webhook, "secret", "")
    assert verify_bugzilla_webhook_secret(BUGZILLA_SECRET) is False


# --- mention detection / loop prevention ---


def _comment_txn(
    phid: str,
    author: str,
    raw: str,
    txn_type: str = "comment",
    *,
    comment_id: int = 1,
    fields: dict | None = None,
) -> dict:
    return {
        "phid": phid,
        "type": txn_type,
        "authorPHID": author,
        "comments": [{"id": comment_id, "content": {"raw": raw}}],
        "fields": fields or {},
    }


def test_find_mention_matches():
    txns = [_comment_txn("PHID-XACT-1", "PHID-USER-a", "hey @hackbot please fix")]
    assert find_hackbot_mentions(
        txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
    ) == [HackbotMention("hey @hackbot please fix", "PHID-USER-a", 1, "regular")]


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
        _comment_txn(
            "PHID-XACT-1",
            "PHID-USER-a",
            "@hackbot here",
            txn_type="inline",
            fields={
                "diff": {"id": 456},
                "path": "browser/foo.cpp",
                "line": 42,
            },
        )
    ]
    assert find_hackbot_mentions(
        txns, {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
    ) == [
        HackbotMention(
            "@hackbot here",
            "PHID-USER-a",
            1,
            "inline",
            diff_id=456,
        )
    ]


def test_find_mention_collects_all_inline_matches():
    # A review with several inline @hackbot comments (each its own transaction)
    # yields all of them, in order; comments without the token are skipped.
    txns = [
        _comment_txn(
            "PHID-XACT-1",
            "PHID-USER-a",
            "@hackbot fix this",
            "inline",
            comment_id=1,
            fields={"diff": {"id": 1}, "path": "a.cpp", "line": 10},
        ),
        _comment_txn(
            "PHID-XACT-2",
            "PHID-USER-a",
            "no mention here",
            "inline",
            comment_id=2,
            fields={"diff": {"id": 2}, "path": "b.cpp", "line": 20},
        ),
        _comment_txn(
            "PHID-XACT-3",
            "PHID-USER-a",
            "@hackbot and this too",
            "inline",
            comment_id=3,
            fields={"diff": {"id": 3}, "path": "c.cpp", "line": 30},
        ),
    ]
    assert find_hackbot_mentions(
        txns,
        {"PHID-XACT-1", "PHID-XACT-2", "PHID-XACT-3"},
        bot_phid="PHID-USER-bot",
        token="@hackbot",
    ) == [
        HackbotMention(
            "@hackbot fix this",
            "PHID-USER-a",
            1,
            "inline",
            diff_id=1,
        ),
        HackbotMention(
            "@hackbot and this too",
            "PHID-USER-a",
            3,
            "inline",
            diff_id=3,
        ),
    ]


def test_find_mention_one_per_transaction_ignores_comment_versions():
    # A transaction's `comments` list is version history, not distinct comments;
    # only one match is taken per transaction.
    txn = {
        "phid": "PHID-XACT-1",
        "type": "inline",
        "authorPHID": "PHID-USER-a",
        "comments": [
            {"id": 456, "content": {"raw": "@hackbot v1"}},
            {"id": 456, "content": {"raw": "@hackbot v2 edited"}},
        ],
        "fields": {"diff": {"id": 456}, "path": "browser/foo.cpp", "line": 42},
    }
    assert find_hackbot_mentions(
        [txn], {"PHID-XACT-1"}, bot_phid="PHID-USER-bot", token="@hackbot"
    ) == [
        HackbotMention(
            "@hackbot v1",
            "PHID-USER-a",
            456,
            "inline",
            diff_id=456,
        )
    ]


def test_format_comment_renders_regular_comment_as_xml():
    mention = HackbotMention("only one", "PHID-USER-a", 123, "regular")
    assert _format_comment(mention) == (
        '  <comment comment_id="123" type="regular">\n    only one\n  </comment>'
    )


def test_format_comment_renders_inline_comment_as_xml():
    mention = HackbotMention(
        "fix this",
        "PHID-USER-a",
        456,
        "inline",
        diff_id=456,
    )
    assert _format_comment(mention) == (
        '  <comment comment_id="456" type="inline" diff_id="456">\n'
        "    fix this\n"
        "  </comment>"
    )


def test_format_comments_renders_mixed_comments_in_order():
    mentions = [
        HackbotMention("first", "PHID-USER-a", 1, "regular"),
        HackbotMention(
            "second",
            "PHID-USER-a",
            2,
            "inline",
            diff_id=456,
        ),
    ]
    formatted = "\n\n".join(_format_comment(mention) for mention in mentions)
    assert formatted == (
        '  <comment comment_id="1" type="regular">\n    first\n  </comment>\n\n'
        '  <comment comment_id="2" type="inline" diff_id="456">\n'
        "    second\n"
        "  </comment>"
    )


def test_format_comment_escapes_comment_body():
    mention = HackbotMention("@hackbot <fix> & explain", "PHID-USER-a", 1, "regular")
    assert _format_comment(mention) == (
        '  <comment comment_id="1" type="regular">\n'
        "    @hackbot &lt;fix&gt; &amp; explain\n"
        "  </comment>"
    )


# --- revision resolution ---


class _FakeClient:
    def __init__(self, revision, members=()):
        self._revision = revision
        self._members = frozenset(members)

    async def search_transactions(self, phid):
        return []

    async def search_revision(self, phid):
        return self._revision

    async def get_project_members(self, project_phid):
        assert project_phid == AUTHORIZED_GROUP_PHID
        return self._members


async def test_resolve_revision_with_bug():
    client = _FakeClient({"id": 42, "fields": {"bugzilla.bug-id": "12345"}})
    assert await resolve_revision(client, "PHID-DREV-x") == (42, 12345)


async def test_resolve_revision_no_bug():
    client = _FakeClient({"id": 42, "fields": {"bugzilla.bug-id": ""}})
    assert await resolve_revision(client, "PHID-DREV-x") == (42, None)


async def test_resolve_revision_not_found():
    client = _FakeClient(None)
    assert await resolve_revision(client, "PHID-DREV-x") == (None, None)


async def test_detect_mention_requires_editbugs_membership(monkeypatch):
    client = _FakeClient(
        {"id": 42, "fields": {"bugzilla.bug-id": "12345"}},
        members={"PHID-USER-authorized"},
    )
    transactions = [
        _comment_txn("PHID-XACT-1", "PHID-USER-unauthorized", "@hackbot ignore"),
    ]
    monkeypatch.setattr(
        client,
        "search_transactions",
        AsyncMock(return_value=transactions),
    )

    result = await detect_mention_and_revision(
        client,
        settings.webhook,
        "PHID-DREV-x",
        ["PHID-XACT-1"],
        authorizer=PhabricatorAuthorizer(client, AUTHORIZED_GROUP_PHID),
    )
    assert result is None


async def test_detect_mention_accepts_editbugs_member(monkeypatch):
    client = _FakeClient(
        {"id": 42, "fields": {"bugzilla.bug-id": "12345"}},
        members={"PHID-USER-authorized"},
    )
    transactions = [
        _comment_txn("PHID-XACT-1", "PHID-USER-authorized", "@hackbot please fix"),
    ]
    monkeypatch.setattr(
        client,
        "search_transactions",
        AsyncMock(return_value=transactions),
    )

    result = await detect_mention_and_revision(
        client,
        settings.webhook,
        "PHID-DREV-x",
        ["PHID-XACT-1"],
        authorizer=PhabricatorAuthorizer(client, AUTHORIZED_GROUP_PHID),
    )
    assert result == (
        '  <comment comment_id="1" type="regular">\n'
        "    @hackbot please fix\n"
        "  </comment>",
        42,
        12345,
    )
    client.search_transactions.assert_awaited_once_with("PHID-DREV-x")


async def test_detect_mention_enriches_inline_anchor(monkeypatch):
    client = _FakeClient(
        {"id": 42, "fields": {"bugzilla.bug-id": "12345"}},
        members={"PHID-USER-authorized"},
    )
    transactions = [
        _comment_txn(
            "PHID-XACT-1",
            "PHID-USER-authorized",
            "@hackbot please fix",
            "inline",
            fields={
                "diff": {"id": 456},
                "path": "browser/foo.cpp",
                "line": 42,
            },
        )
    ]
    monkeypatch.setattr(
        client,
        "search_transactions",
        AsyncMock(return_value=transactions),
    )

    result = await detect_mention_and_revision(
        client,
        settings.webhook,
        "PHID-DREV-x",
        ["PHID-XACT-1"],
        authorizer=PhabricatorAuthorizer(client, AUTHORIZED_GROUP_PHID),
    )
    assert result == (
        '  <comment comment_id="1" type="inline" diff_id="456">\n'
        "    @hackbot please fix\n"
        "  </comment>",
        42,
        12345,
    )


# --- payload parsing ---


def test_triggering_transaction_phids():
    payload = {"transactions": [{"phid": "A"}, {"phid": "B"}, {"nophid": True}]}
    assert triggering_transaction_phids(payload) == ["A", "B"]


def _bugzilla_payload(
    *,
    bug_id: int = 2022889,
    flag_id: int = 2187233,
    added: str = "? (hackbot@mozilla.tld)",
    removed: str = "",
    requestee: str = BUGZILLA_BOT_LOGIN,
    actor: str = "gmierzwinski@mozilla.com",
    event_time: str = "2026-08-07T18:00:05",
) -> dict:
    return {
        "bug": {
            "id": bug_id,
            "is_private": False,
            "flags": [
                {
                    "id": flag_id,
                    "name": "needinfo",
                    "requestee": {"login": requestee},
                    "value": "?",
                }
            ],
        },
        "event": {
            "action": "modify",
            "changes": [
                {
                    "added": added,
                    "field": "flag.needinfo",
                    "removed": removed,
                }
            ],
            "routing_key": "bug.modify:flag.needinfo",
            "target": "bug",
            "time": event_time,
            "user": {
                "id": 560562,
                "login": actor,
                "real_name": "Greg Mierzwinski [:sparky]",
            },
        },
        "webhook_id": 121,
        "webhook_name": "Hackbot needinfo dry run",
    }


def test_detect_bugzilla_needinfo_from_captured_payload_shape():
    detected = detect_needinfo_request(
        _bugzilla_payload(), bot_login=BUGZILLA_BOT_LOGIN
    )
    assert detected is not None
    assert detected.bug_id == 2022889
    assert detected.flag_id == 2187233
    assert detected.dedupe_key == "ni2187233"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("event"),
        lambda payload: payload.pop("bug"),
        lambda payload: payload["event"].pop("changes"),
        lambda payload: payload["bug"].pop("id"),
        lambda payload: payload["bug"].pop("flags"),
    ],
)
def test_detect_bugzilla_needinfo_rejects_malformed_nested_fields(mutate):
    payload = _bugzilla_payload()
    mutate(payload)
    with pytest.raises(KeyError):
        detect_needinfo_request(payload, bot_login=BUGZILLA_BOT_LOGIN)


def test_detect_bugzilla_needinfo_does_not_require_routing_key():
    payload = _bugzilla_payload()
    payload["event"]["routing_key"] = "bug.modify:summary,flag.needinfo"
    assert detect_needinfo_request(payload, bot_login=BUGZILLA_BOT_LOGIN) is not None


@pytest.mark.parametrize(
    "flag_update",
    [
        {"id": 0},
        {"name": "review"},
        {"value": "+"},
        {"requestee": {"login": "someone@mozilla.com"}},
    ],
)
def test_detect_bugzilla_needinfo_requires_matching_structured_flag(flag_update):
    payload = _bugzilla_payload()
    payload["bug"]["flags"][0].update(flag_update)
    assert detect_needinfo_request(payload, bot_login=BUGZILLA_BOT_LOGIN) is None


# --- route ---


class _FakeHackbotClient:
    """Stub for HackbotClient, injected via dependency_overrides."""

    def __init__(self):
        self.calls = []

    async def trigger_run(self, agent_name, inputs):
        self.calls.append((agent_name, inputs))
        return "run-abc"


class _FakeAuthorizer:
    async def is_authorized(self, author_phid):
        return True


@pytest.fixture
def authorizer():
    return _FakeAuthorizer()


@pytest.fixture
def phab_client():
    return object()


@pytest.fixture
def client(monkeypatch, authorizer, phab_client):
    monkeypatch.setattr(settings.webhook, "secret", SECRET)
    monkeypatch.setattr(settings.bugzilla_webhook, "secret", BUGZILLA_SECRET)
    monkeypatch.setattr(settings.bugzilla_webhook, "bot_login", BUGZILLA_BOT_LOGIN)
    # Fresh dedupe cache per test.
    webhooks._seen_transactions.clear()
    webhooks._seen_bugzilla_events.clear()
    app.dependency_overrides[webhooks.get_phabricator_client] = lambda: phab_client
    app.dependency_overrides[webhooks.get_phabricator_authorizer] = lambda: authorizer
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


def _post_bugzilla(client, payload: dict, secret: str = BUGZILLA_SECRET):
    return client.post(
        "/webhooks/bugzilla",
        json=payload,
        headers={"X-Bugzilla-Webhook-Secret": secret},
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


def test_route_triggers_run(client, phab_client, authorizer, monkeypatch):
    detect = AsyncMock(return_value=("@hackbot please fix", 42, 12345))
    monkeypatch.setattr(webhooks, "detect_mention_and_revision", detect)
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
    assert detect.call_args.args[0] is phab_client
    assert detect.call_args.kwargs["authorizer"] is authorizer
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


def test_route_detects_only_fresh_transactions(client, monkeypatch):
    # A delivery mixing an already-seen PHID with a new one must consider only
    # the fresh transaction for mention detection.
    detect = AsyncMock(return_value=("@hackbot please fix", 42, 12345))
    monkeypatch.setattr(webhooks, "detect_mention_and_revision", detect)
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: _FakeHackbotClient()
    webhooks._seen_transactions["PHID-XACT-OLD"] = True

    _post(
        client,
        {
            "object": {"type": "DREV", "phid": "PHID-DREV-1"},
            "transactions": [{"phid": "PHID-XACT-OLD"}, {"phid": "PHID-XACT-NEW"}],
        },
    )
    assert detect.call_args.args[3] == ["PHID-XACT-NEW"]


def test_route_does_not_mark_seen_on_trigger_failure(client, monkeypatch):
    # A transient failure must not consume the delivery: the transaction stays
    # unseen so Phabricator's retry is reprocessed rather than deduped away.
    monkeypatch.setattr(
        webhooks,
        "detect_mention_and_revision",
        AsyncMock(return_value=("@hackbot please fix", 42, 12345)),
    )

    class _FailingClient:
        async def trigger_run(self, agent_name, inputs):
            raise RuntimeError("conduit down")

    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: _FailingClient()

    with pytest.raises(RuntimeError):
        _post(
            client,
            {
                "object": {"type": "DREV", "phid": "PHID-DREV-1"},
                "transactions": [{"phid": "PHID-XACT-1"}],
            },
        )
    assert "PHID-XACT-1" not in webhooks._seen_transactions


def test_bugzilla_route_ignores_non_object_payload(client):
    resp = _post_bugzilla(client, [])
    assert resp.status_code == 202
    assert resp.json() == {
        "status": "ignored",
        "reason": "payload is not a JSON object",
    }


def test_bugzilla_route_rejects_bad_secret(client):
    response = _post_bugzilla(client, _bugzilla_payload(), secret="wrong")
    assert response.status_code == 401


def test_bugzilla_route_ignores_non_matching_event(client):
    response = _post_bugzilla(
        client,
        _bugzilla_payload(added="? (someone@mozilla.com)"),
    )
    assert response.status_code == 202
    assert response.json() == {
        "status": "ignored",
        "reason": "no actionable Hackbot needinfo",
    }


def test_bugzilla_route_triggers_run(client):
    fake_api = _FakeHackbotClient()
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: fake_api

    response = _post_bugzilla(client, _bugzilla_payload())

    assert response.status_code == 202
    assert response.json() == {"status": "triggered", "run_id": "run-abc"}
    assert fake_api.calls == [
        (
            "bug-fix",
            {"bug_id": 2022889, "bugzilla_needinfo_flag_id": 2187233},
        )
    ]


def test_bugzilla_route_dedupes_retry_but_not_later_event(client):
    fake_api = _FakeHackbotClient()
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: fake_api
    payload = _bugzilla_payload()

    first = _post_bugzilla(client, payload)
    duplicate = _post_bugzilla(client, payload)
    later = _post_bugzilla(
        client,
        _bugzilla_payload(flag_id=2187234, event_time="2026-08-07T19:00:05"),
    )

    assert first.json()["status"] == "triggered"
    assert duplicate.json()["reason"] == "duplicate delivery"
    assert later.json()["status"] == "triggered"
    assert len(fake_api.calls) == 2


def test_bugzilla_route_does_not_dedupe_failed_dispatch(client):
    class _FailingClient:
        async def trigger_run(self, agent_name, inputs):
            raise RuntimeError("run creation failed")

    payload = _bugzilla_payload()
    detected = detect_needinfo_request(payload, bot_login=BUGZILLA_BOT_LOGIN)
    assert detected is not None
    app.dependency_overrides[webhooks.get_hackbot_client] = lambda: _FailingClient()

    with pytest.raises(RuntimeError, match="run creation failed"):
        _post_bugzilla(client, payload)

    assert detected.dedupe_key not in webhooks._seen_bugzilla_events
