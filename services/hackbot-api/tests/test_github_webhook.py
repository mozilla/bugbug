"""Tests for the GitHub webhook receiver.

Covers X-Hub-Signature-256 verification, mention detection and loop prevention,
the PR head lookup, and the route's ignore/trigger branches. GitHub itself is
mocked; the authorizer has its own tests in test_github_authorization.py.
"""

import asyncio
import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.auth import verify_github_signature
from app.config import GitHubAppSettings, Settings, settings
from app.github import GitHubApp
from app.github_authorization import GitHubAuthorizer
from app.github_webhook import (
    GitHubMention,
    _format_comment,
    detect_mention,
    mentions,
    resolve_head_sha,
)
from app.hackbot_client import get_hackbot_client
from app.main import app
from app.routers import webhooks
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from hackbot_client import RunStatus, TriggeredRun
from pydantic import ValidationError

SECRET = "test-github-webhook-secret"
BOT_LOGIN = "hackbot"
TOKEN = "@hackbot"
INSTALLATION_ID = 456
HEAD_SHA = "a" * 40


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- signature verification ---


def test_signature_valid():
    body = b'{"a": 1}'
    assert verify_github_signature(body, _sign(body), SECRET) is True


def test_signature_wrong_secret():
    body = b'{"a": 1}'
    assert verify_github_signature(body, _sign(body, "other"), SECRET) is False


def test_signature_tampered_body():
    assert verify_github_signature(b"other", _sign(b"body"), SECRET) is False


def test_signature_missing_header():
    assert verify_github_signature(b"body", None, SECRET) is False


def test_signature_requires_sha256_prefix():
    # The bare digest, and the legacy SHA-1 header's format, are both refused.
    body = b"body"
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    sha1 = hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
    assert verify_github_signature(body, digest, SECRET) is False
    assert verify_github_signature(body, f"sha1={sha1}", SECRET) is False


def test_signature_non_ascii_is_rejected():
    # compare_digest raises TypeError on non-ASCII str arguments.
    assert verify_github_signature(b"body", "sha256=é", SECRET) is False


def test_signature_unconfigured_secret():
    assert verify_github_signature(b"body", _sign(b"body", ""), "") is False


# --- mention matching ---


@pytest.mark.parametrize(
    "body",
    [
        "@hackbot please fix this",
        "Can you take a look, @hackbot?",
        "@HackBot fix",
        "line one\n@hackbot, line two",
        "(@hackbot)",
        "**@hackbot** fix",
        "- @hackbot fix",
        # A lazy paragraph continuation, not an indented code block.
        "Please\n    @hackbot fix",
        "`code` then @hackbot",
    ],
)
def test_mentions_matches(body):
    assert mentions(body, TOKEN) is True


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "no mention here",
        "@hackbot-dev fix",
        "@hackbot_x fix",
        "mail me@hackbot",
        "hackbot without the at",
        "> @hackbot fix this\n\nI agree",
        "   > nested @hackbot quote",
        "use `@hackbot fix` to ask",
        "```\n@hackbot fix\n```",
        "~~~text\n@hackbot fix\n~~~",
        "- > @hackbot in a quoted list item",
        "a ``@hackbot`` code span",
        "a ``code with ` and @hackbot`` span",
        "a `code span\n@hackbot over two lines` here",
        "    @hackbot indented code",
        "````\n```\n@hackbot inside a longer fence\n````",
        "[@hackbot](https://example.com)",
        # Removing the code must not join the halves into a handle.
        "@hack`x`bot",
        "@hack*bot*",
    ],
)
def test_mentions_ignores(body):
    assert mentions(body, TOKEN) is False


def test_mentions_after_closed_fence():
    assert mentions("```\ncode\n```\n@hackbot fix", TOKEN) is True


def test_mentions_fence_closes_only_on_same_marker():
    assert mentions("```\n~~~\n@hackbot\n```", TOKEN) is False


def test_mentions_empty_token():
    assert mentions("@hackbot", "") is False


# --- comment formatting ---


def test_format_comment_escapes_body_and_attributes():
    rendered = _format_comment(
        "</comment> & <b>", {"comment_id": 1, "type": "inline", "path": 'a"b<c'}
    )
    assert rendered == (
        '  <comment comment_id="1" type="inline" path=\'a"b&lt;c\'>\n'
        "    &lt;/comment&gt; &amp; &lt;b&gt;\n"
        "  </comment>"
    )


def test_format_comment_skips_missing_attributes():
    rendered = _format_comment("hi", {"comment_id": 1, "line": None})
    assert rendered == '  <comment comment_id="1">\n    hi\n  </comment>'


# --- payloads ---


def _base(action: str, sender: dict | None = None) -> dict:
    return {
        "action": action,
        "installation": {"id": INSTALLATION_ID},
        "repository": {
            "name": "gecko-dev",
            "full_name": "mozilla/gecko-dev",
            "owner": {"login": "mozilla"},
        },
        "sender": sender or {"login": "dev", "type": "User"},
    }


def _issue_comment(body: str = "@hackbot fix", **overrides) -> dict:
    payload = _base(overrides.pop("action", "created"), overrides.pop("sender", None))
    payload["issue"] = {
        "number": 7,
        "state": overrides.pop("state", "open"),
        "pull_request": overrides.pop(
            "pull_request", {"url": "https://api.github.com/repos/x/y/pulls/7"}
        ),
    }
    payload["comment"] = {"id": 1001, "body": body, "user": {"login": "dev"}}
    assert not overrides, overrides
    return payload


def _pull_request(state: str = "open") -> dict:
    return {"number": 7, "state": state, "head": {"sha": HEAD_SHA}}


def _review_comment(body: str = "@hackbot fix this", **overrides) -> dict:
    payload = _base(overrides.pop("action", "created"), overrides.pop("sender", None))
    payload["pull_request"] = _pull_request(overrides.pop("state", "open"))
    payload["comment"] = {
        "id": 2002,
        "body": body,
        "path": "dom/base/Foo.cpp",
        "line": overrides.pop("line", 42),
        "original_line": 40,
        "commit_id": "b" * 40,
        "pull_request_review_id": 3003,
    }
    assert not overrides, overrides
    return payload


def _review(body: str | None = "@hackbot address these", **overrides) -> dict:
    payload = _base(overrides.pop("action", "submitted"), overrides.pop("sender", None))
    payload["pull_request"] = _pull_request(overrides.pop("state", "open"))
    payload["review"] = {"id": 3003, "body": body, "state": "commented"}
    assert not overrides, overrides
    return payload


def _detect(event: str, payload: dict) -> GitHubMention | None:
    return detect_mention(event, payload, mention_token=TOKEN)


# --- detection ---


def test_detect_issue_comment_on_pr():
    assert _detect("issue_comment", _issue_comment()) == GitHubMention(
        installation_id=INSTALLATION_ID,
        owner="mozilla",
        repo="gecko-dev",
        pr_number=7,
        sender={"login": "dev", "type": "User"},
        comment=(
            '  <comment comment_id="1001" type="regular">\n'
            "    @hackbot fix\n"
            "  </comment>"
        ),
        dedupe_key="github-issue-comment:1001",
        # Not in an issue_comment payload; looked up by the route.
        head_sha=None,
    )


def test_detect_review_comment():
    mention = _detect("pull_request_review_comment", _review_comment())
    assert mention is not None
    assert mention.full_name == "mozilla/gecko-dev"
    assert mention.pr_number == 7
    assert mention.head_sha == HEAD_SHA
    assert mention.dedupe_key == "github-review-comment:2002"
    assert mention.comment == (
        '  <comment comment_id="2002" type="inline" path="dom/base/Foo.cpp" '
        f'line="42" commit_id="{"b" * 40}">\n'
        "    @hackbot fix this\n"
        "  </comment>"
    )


def test_detect_review_comment_on_outdated_line_uses_original_line():
    # GitHub nulls ``line`` once the commented line no longer exists.
    mention = _detect("pull_request_review_comment", _review_comment(line=None))
    assert mention is not None
    assert 'line="40"' in mention.comment


def test_detect_review():
    mention = _detect("pull_request_review", _review())
    assert mention is not None
    assert mention.head_sha == HEAD_SHA
    assert mention.dedupe_key == "github-review:3003"
    assert mention.comment.startswith('  <comment review_id="3003" type="review">')


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        # Edits and deletions never trigger, only new comments.
        ("issue_comment", _issue_comment(action="edited")),
        ("issue_comment", _issue_comment(action="deleted")),
        ("pull_request_review_comment", _review_comment(action="edited")),
        ("pull_request_review", _review(action="edited")),
        ("pull_request_review", _review(action="dismissed")),
        # A plain issue, not a PR.
        ("issue_comment", _issue_comment(pull_request=None)),
        # Closed (or merged) PRs.
        ("issue_comment", _issue_comment(state="closed")),
        ("pull_request_review_comment", _review_comment(state="closed")),
        ("pull_request_review", _review(state="closed")),
        # No mention, or an approval with no body.
        ("issue_comment", _issue_comment("looks good")),
        ("pull_request_review", _review(None)),
        # Events Hackbot doesn't act on.
        ("pull_request", _review()),
        ("push", {}),
        (None, _issue_comment()),
    ],
)
def test_detect_ignores(event, payload):
    assert _detect(event, payload) is None


def test_detect_leaves_bots_to_the_authorizer():
    sender = {"login": "hackbot[bot]", "type": "Bot"}
    mention = _detect("issue_comment", _issue_comment(sender=sender))
    assert mention is not None
    assert mention.sender == sender


def test_detect_reads_targets_from_payload_not_comment_text():
    payload = _issue_comment("@hackbot fix evil/repo#99")
    mention = _detect("issue_comment", payload)
    assert mention is not None
    assert (mention.full_name, mention.pr_number) == ("mozilla/gecko-dev", 7)


# --- head lookup ---


@pytest.fixture(scope="module")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


class _FakeGitHub:
    """Stands in for api.github.com: mints tokens and serves one pull request."""

    def __init__(self, state: str = "open", delay: float = 0):
        self.state = state
        self.delay = delay
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        path = request.url.path
        if path.endswith("/access_tokens"):
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            return httpx.Response(
                201,
                json={
                    "token": "ghs_test",
                    "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        if path == "/repos/mozilla/gecko-dev/pulls/7":
            return httpx.Response(200, json=_pull_request_response(self.state))
        return httpx.Response(500, json={"message": f"unexpected {path}"})


def _pull_request_response(state: str) -> dict:
    # Trimmed: the lookup reads only these fields.
    return {"number": 7, "state": state, "head": {"sha": HEAD_SHA}}


def _github_app(pem: str, fake: _FakeGitHub) -> GitHubApp:
    return GitHubApp(
        GitHubAppSettings(app_id=123, private_key=pem),
        async_transport=httpx.MockTransport(fake),
        http_cache=False,
        auto_retry=False,
    )


async def test_resolve_head_sha_reads_open_pr_with_a_narrow_token(pem):
    fake = _FakeGitHub()
    mention = _detect("issue_comment", _issue_comment())

    assert await resolve_head_sha(_github_app(pem, fake), mention) == HEAD_SHA

    mint = next(r for r in fake.requests if r.url.path.endswith("/access_tokens"))
    assert mint.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    assert json.loads(mint.content) == {
        "permissions": {"pull_requests": "read"},
        "repositories": ["gecko-dev"],
    }


async def test_resolve_head_sha_of_closed_pr_is_none(pem):
    mention = _detect("issue_comment", _issue_comment())
    assert (
        await resolve_head_sha(_github_app(pem, _FakeGitHub("closed")), mention) is None
    )


async def test_resolve_head_sha_deadline_covers_token_mint_and_lookup(pem):
    # Each request alone fits the deadline; the mint and the fetch together don't.
    github_app = _github_app(pem, _FakeGitHub(delay=0.06))
    mention = _detect("issue_comment", _issue_comment())
    with pytest.raises(TimeoutError):
        await resolve_head_sha(github_app, mention, timeout_seconds=0.1)


# --- route ---


class _FakeHackbotClient:
    """Stub for HackbotClient, injected via dependency_overrides."""

    def __init__(self):
        self.calls = []
        self.dedupe_keys = []
        self.delay = 0.0

    async def trigger_run(self, agent_name, inputs, *, dedupe_key=None):
        self.calls.append((agent_name, inputs))
        is_new = dedupe_key is None or dedupe_key not in self.dedupe_keys
        self.dedupe_keys.append(dedupe_key)
        # After recording: like the real API, the run exists even if the caller
        # stops waiting for the response.
        await asyncio.sleep(self.delay)
        return TriggeredRun(
            run_id="d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6",
            agent=agent_name,
            status=RunStatus.pending,
            is_new=is_new,
        )


class _FakeAuthorizer:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed
        self.checked = []

    async def is_authorized(self, installation_id, owner, repo, sender):
        self.checked.append((installation_id, owner, repo, sender))
        return self.allowed


@pytest.fixture
def authorizer():
    return _FakeAuthorizer()


@pytest.fixture
def api():
    return _FakeHackbotClient()


@pytest.fixture
def github_app():
    return object()


@pytest.fixture
def head_lookups(monkeypatch):
    calls = []

    async def fake_resolve(github_app, mention):
        calls.append((github_app, mention))
        return HEAD_SHA

    monkeypatch.setattr(webhooks, "resolve_head_sha", fake_resolve)
    return calls


def _app_settings(pem: str, **overrides) -> GitHubAppSettings:
    return GitHubAppSettings(
        **{"app_id": 123, "private_key": pem, "webhook_secret": SECRET, **overrides}
    )


@pytest.fixture
def client(monkeypatch, pem, authorizer, api, github_app, head_lookups):
    monkeypatch.setattr(settings, "github", _app_settings(pem))
    app.dependency_overrides[webhooks.get_github_app] = lambda: github_app
    app.dependency_overrides[webhooks.get_github_authorizer] = lambda: authorizer
    app.dependency_overrides[get_hackbot_client] = lambda: api
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _post(client, event: str, payload: dict, *, signature: str | None = None):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
            "X-Hub-Signature-256": signature or _sign(body),
        },
    )


def test_route_rejects_bad_signature(client, api):
    resp = _post(client, "issue_comment", _issue_comment(), signature="sha256=00")
    assert resp.status_code == 401
    assert api.calls == []


def test_route_rejects_non_ascii_signature(client, api):
    resp = client.post(
        "/webhooks/github",
        content=json.dumps(_issue_comment()).encode(),
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": "sha256=é".encode(),
        },
    )
    assert resp.status_code == 401
    assert api.calls == []


def test_route_rejects_missing_signature(client):
    resp = client.post(
        "/webhooks/github",
        json=_issue_comment(),
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("header", ["X-GitHub-Event", "X-GitHub-Delivery"])
def test_route_rejects_signed_delivery_missing_header(client, api, header):
    # GitHub sends both on every delivery, so a request without one is malformed.
    body = json.dumps(_issue_comment()).encode()
    headers = {
        "X-GitHub-Event": "issue_comment",
        "X-GitHub-Delivery": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
        "X-Hub-Signature-256": _sign(body),
    }
    del headers[header]
    resp = client.post("/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 422
    assert api.calls == []


def test_route_unavailable_without_secret(client, monkeypatch, pem, api):
    monkeypatch.setattr(settings, "github", _app_settings(pem, webhook_secret=None))
    resp = _post(client, "issue_comment", _issue_comment())
    assert resp.status_code == 503
    assert api.calls == []


def test_route_unavailable_without_github_app(client, monkeypatch, api):
    monkeypatch.setattr(settings, "github", None)
    resp = _post(client, "issue_comment", _issue_comment())
    assert resp.status_code == 503
    assert api.calls == []


def test_route_answers_ping(client, api):
    resp = _post(client, "ping", {"zen": "Keep it logically awesome."})
    assert resp.status_code == 202
    assert resp.json() == {"status": "ignored", "reason": "ping"}
    assert api.calls == []


def test_route_ignores_non_object_payload(client):
    resp = _post(client, "issue_comment", [])
    assert resp.json() == {
        "status": "ignored",
        "reason": "payload is not a JSON object",
    }


def test_route_ignores_no_mention(client, api, authorizer):
    resp = _post(client, "issue_comment", _issue_comment("thanks!"))
    assert resp.status_code == 202
    assert resp.json() == {"status": "ignored", "reason": "no actionable mention"}
    assert authorizer.checked == []
    assert api.calls == []


def test_route_ignores_unauthorized_sender(client, api, authorizer, head_lookups):
    authorizer.allowed = False
    resp = _post(client, "issue_comment", _issue_comment())
    assert resp.json() == {"status": "ignored", "reason": "unauthorized user"}
    assert authorizer.checked == [
        (INSTALLATION_ID, "mozilla", "gecko-dev", {"login": "dev", "type": "User"})
    ]
    # Unauthorized senders cost no further GitHub calls.
    assert head_lookups == []
    assert api.calls == []


@pytest.mark.parametrize(
    "sender",
    [
        {"login": "hackbot[bot]", "type": "Bot"},
        {"login": "dependabot[bot]", "type": "Bot"},
        {"login": BOT_LOGIN, "type": "User"},
    ],
)
def test_route_ignores_bot_senders(client, api, head_lookups, sender):
    # Hackbot's own replies quote the mention, so this is loop prevention. The
    # real authorizer rejects bots before any GitHub call, so no App is needed.
    app.dependency_overrides[webhooks.get_github_authorizer] = lambda: GitHubAuthorizer(
        object(), {"write"}, BOT_LOGIN
    )
    resp = _post(client, "issue_comment", _issue_comment(sender=sender))
    assert resp.json() == {"status": "ignored", "reason": "unauthorized user"}
    assert head_lookups == []
    assert api.calls == []


@pytest.mark.parametrize("missing", ["null", "absent"])
def test_route_ignores_missing_sender(client, api, head_lookups, missing):
    app.dependency_overrides[webhooks.get_github_authorizer] = lambda: GitHubAuthorizer(
        object(), {"write"}, BOT_LOGIN
    )
    payload = _issue_comment()
    if missing == "null":
        payload["sender"] = None
    else:
        del payload["sender"]
    resp = _post(client, "issue_comment", payload)
    assert resp.status_code == 202
    assert resp.json() == {"status": "ignored", "reason": "unauthorized user"}
    assert head_lookups == []
    assert api.calls == []


def test_route_triggers_run_for_conversation_comment(
    client, api, github_app, head_lookups
):
    resp = _post(client, "issue_comment", _issue_comment())

    assert resp.status_code == 202
    assert resp.json() == {
        "status": "triggered",
        "run_id": "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6",
    }
    assert [(app_, m.dedupe_key) for app_, m in head_lookups] == [
        (github_app, "github-issue-comment:1001")
    ]
    assert api.calls == [
        (
            "bug-fix",
            {
                "github_repo": "mozilla/gecko-dev",
                "github_pr_number": 7,
                "github_head_sha": HEAD_SHA,
                "comment": (
                    '  <comment comment_id="1001" type="regular">\n'
                    "    @hackbot fix\n"
                    "  </comment>"
                ),
            },
        )
    ]
    assert api.dedupe_keys == ["github-issue-comment:1001"]


@pytest.mark.parametrize(
    ("event", "payload", "dedupe_key"),
    [
        (
            "pull_request_review_comment",
            _review_comment(),
            "github-review-comment:2002",
        ),
        ("pull_request_review", _review(), "github-review:3003"),
    ],
)
def test_route_triggers_run_for_review_without_head_lookup(
    client, api, head_lookups, event, payload, dedupe_key
):
    resp = _post(client, event, payload)

    assert resp.json()["status"] == "triggered"
    # The review payload already carries the head.
    assert head_lookups == []
    assert api.calls[0][1]["github_head_sha"] == HEAD_SHA
    assert api.dedupe_keys == [dedupe_key]


def test_route_ignores_pr_closed_before_lookup(client, api, monkeypatch):
    async def closed(github_app, mention):
        return None

    monkeypatch.setattr(webhooks, "resolve_head_sha", closed)
    resp = _post(client, "issue_comment", _issue_comment())
    assert resp.json() == {"status": "ignored", "reason": "pull request is not open"}
    assert api.calls == []


def test_route_fails_delivery_when_head_lookup_times_out(client, api, monkeypatch):
    # Failed, the delivery can be redelivered from the App's settings; no run was
    # created, so its key isn't spent.
    async def slow(github_app, mention):
        raise TimeoutError

    monkeypatch.setattr(webhooks, "resolve_head_sha", slow)
    resp = _post(client, "issue_comment", _issue_comment())
    assert resp.status_code == 503
    assert api.calls == []


def test_route_deadline_covers_the_whole_delivery(client, api, monkeypatch):
    # Each step is quick, but together they outlast the deadline.
    monkeypatch.setattr(webhooks, "DELIVERY_DEADLINE_SECONDS", 0.1)

    async def slow(github_app, mention):
        await asyncio.sleep(0.06)
        return HEAD_SHA

    monkeypatch.setattr(webhooks, "resolve_head_sha", slow)
    api.delay = 0.06
    resp = _post(client, "issue_comment", _issue_comment())
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Timed out handling the delivery"}


def test_route_redelivery_after_trigger_timeout_reuses_the_run(
    client, api, monkeypatch
):
    # The run was created, but its response came too late: the redelivery has
    # the same dedupe key, so it resolves to that run instead of a second one.
    monkeypatch.setattr(webhooks, "DELIVERY_DEADLINE_SECONDS", 0.05)
    payload = _review_comment()
    api.delay = 1.0
    timed_out = _post(client, "pull_request_review_comment", payload)
    api.delay = 0.0
    redelivery = _post(client, "pull_request_review_comment", payload)

    assert timed_out.status_code == 503
    assert redelivery.json() == {
        "status": "ignored",
        "reason": "duplicate delivery",
        "run_id": "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6",
    }
    assert api.dedupe_keys == ["github-review-comment:2002"] * 2


def test_route_dedupes_redelivery_but_not_a_new_comment(client, api):
    payload = _issue_comment()
    first = _post(client, "issue_comment", payload)
    redelivery = _post(client, "issue_comment", payload)
    later = copy.deepcopy(payload)
    later["comment"]["id"] = 1002
    new_comment = _post(client, "issue_comment", later)

    assert first.json()["status"] == "triggered"
    assert redelivery.json() == {
        "status": "ignored",
        "reason": "duplicate delivery",
        "run_id": "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6",
    }
    assert new_comment.json()["status"] == "triggered"
    assert api.dedupe_keys == [
        "github-issue-comment:1001",
        "github-issue-comment:1001",
        "github-issue-comment:1002",
    ]


def test_route_surfaces_trigger_failure(client):
    # A failure before the run exists spends no key, so a manual redelivery from
    # the App's settings is processed rather than deduped away.
    class _FailingClient:
        async def trigger_run(self, agent_name, inputs, *, dedupe_key=None):
            raise RuntimeError("api down")

    app.dependency_overrides[get_hackbot_client] = lambda: _FailingClient()
    with pytest.raises(RuntimeError, match="api down"):
        _post(client, "issue_comment", _issue_comment())


# --- dependencies ---


class _Request:
    def __init__(self):
        self.app = type("App", (), {"state": type("State", (), {})()})()


def test_app_and_authorizer_share_one_github_app(monkeypatch, pem):
    monkeypatch.setattr(
        webhooks.settings,
        "github",
        GitHubAppSettings(app_id=123, private_key=pem),
    )
    request = _Request()

    github_app = webhooks.get_github_app(request)
    assert webhooks.get_github_app(request) is github_app
    # One token cache for the authorizer's lookups and the route's.
    assert webhooks.get_github_authorizer(request)._github_app is github_app
    assert github_app._github_options["auto_retry"] is False


# --- settings ---


def test_settings_read_webhook_secret_env(monkeypatch, pem):
    monkeypatch.delenv("GITHUB_INSTALLATION_ID", raising=False)
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY", pem)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", f"  {SECRET}  ")

    loaded = Settings(_env_file=None)

    # GITHUB_WEBHOOK_SECRET binds to github.webhook_secret, stripped.
    assert loaded.github.webhook_secret == SECRET


def test_webhook_secret_defaults_to_unset(pem):
    assert GitHubAppSettings(app_id=123, private_key=pem).webhook_secret is None


@pytest.mark.parametrize("secret", ["", "   "])
def test_webhook_secret_rejects_blank(pem, secret):
    with pytest.raises(ValidationError, match="webhook_secret"):
        GitHubAppSettings(app_id=123, private_key=pem, webhook_secret=secret)
