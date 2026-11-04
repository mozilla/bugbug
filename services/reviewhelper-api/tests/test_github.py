import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app import github, github_reviews, review_processor, tasks
from app.config import settings
from app.database.connection import get_db
from app.database.models import ReviewRequest
from app.enums import Platform, ReviewStatus
from app.routers import github as routes
from fastapi import FastAPI

SHA = "a" * 40
BASE = "b" * 40
DIFF = "--- a/f.rs\n+++ b/f.rs\n@@ -1 +1 @@\n-old\n+new\n"


def response(body, status=200):
    kwargs = {"text": body} if isinstance(body, str) else {"json": body}
    return httpx.Response(
        status, request=httpx.Request("GET", "https://api.github.com/test"), **kwargs
    )


def pull_request(**kwargs):
    return {
        "number": 15,
        "state": "open",
        "draft": False,
        "head": {"sha": SHA},
        "base": {"sha": BASE, "repo": {"full_name": "owner/repo"}},
        "user": {"id": 7},
        **kwargs,
    }


def review():
    return ReviewRequest(
        id=10,
        platform=Platform.GITHUB,
        owner="owner",
        repo="repo",
        pr_number=15,
        sha=SHA,
        status=ReviewStatus.PENDING,
        general_comment="Check the error handling.",
    )


@pytest.fixture(autouse=True)
def enrolled(monkeypatch):
    monkeypatch.setattr(settings, "github_repositories", ["Owner/Repo"])


@pytest.fixture
def harness(monkeypatch):
    db = AsyncMock()
    db.add = MagicMock()
    monkeypatch.setattr(settings, "github_webhook_secret", "test-webhook-secret")
    app = FastAPI()
    app.include_router(routes.router)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    enqueue = AsyncMock(return_value=review())
    monkeypatch.setattr(routes, "request_github_review", enqueue)
    api = SimpleNamespace(
        request=AsyncMock(return_value=response({"permission": "read"}))
    )
    github_app = SimpleNamespace(
        client=MagicMock(return_value=api),
        repo_client=AsyncMock(return_value=api),
        invalidate=MagicMock(),
    )
    monkeypatch.setattr(routes, "get_github_app", lambda: github_app)
    return app, db, enqueue, api, github_app


def payload(event="pull_request", action="opened"):
    data = {
        "action": action,
        "installation": {"id": 200},
        "repository": {"id": 100, "full_name": "owner/repo"},
        "sender": {"id": 7, "login": "author", "type": "User"},
    }
    if event == "pull_request":
        data["pull_request"] = pull_request()
    else:
        data["issue"] = {"number": 15, "pull_request": {"url": "example"}}
        data["comment"] = {"body": "/review"}
    return data


async def deliver(app, data, event="pull_request", valid_signature=True):
    body = json.dumps(data).encode()
    signature = (
        "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": event,
                "X-Hub-Signature-256": signature if valid_signature else "invalid",
            },
        )


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(harness):
    app, _, enqueue, *_ = harness
    assert (await deliver(app, payload(), valid_signature=False)).status_code == 401
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["opened", "synchronize", "ready_for_review", "reopened"]
)
async def test_automatic_review_on_open_and_repush(harness, action):
    app, _, enqueue, *_ = harness
    assert (await deliver(app, payload(action=action))).status_code == 204
    assert enqueue.await_args.args[3] == 15


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "automatic,repositories", [(False, ["owner/repo"]), (True, ["owner/other"])]
)
async def test_automatic_policy_and_enrollment(
    harness, monkeypatch, automatic, repositories
):
    app, _, enqueue, *_ = harness
    monkeypatch.setattr(settings, "github_automatic_reviews", automatic)
    monkeypatch.setattr(settings, "github_repositories", repositories)
    assert (await deliver(app, payload())).status_code == 204
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_trigger_works_in_manual_only_mode(harness, monkeypatch):
    app, _, enqueue, api, _ = harness
    monkeypatch.setattr(settings, "github_automatic_reviews", False)
    api.request.return_value = response(pull_request())
    assert (
        await deliver(app, payload("issue_comment", "created"), "issue_comment")
    ).status_code == 204
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission,allowed", [("read", False), ("write", True), ("admin", True)]
)
async def test_manual_trigger_checks_non_author_permissions(
    harness, permission, allowed
):
    app, _, enqueue, api, _ = harness
    api.request.side_effect = [
        response(pull_request()),
        response({"permission": permission}),
    ]
    data = payload("issue_comment", "created")
    data["sender"]["id"] = 9
    assert (await deliver(app, data, "issue_comment")).status_code == 204
    assert enqueue.await_count == int(allowed)


@pytest.mark.asyncio
async def test_disabled_author_requests_require_repository_write_access(
    harness, monkeypatch
):
    app, _, enqueue, api, _ = harness
    monkeypatch.setattr(settings, "github_allow_author_requests", False)
    api.request.side_effect = [
        response(pull_request()),
        response({"permission": "read"}),
    ]
    await deliver(app, payload("issue_comment", "created"), "issue_comment")
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_uninstall_forgets_installation_credentials(harness):
    app, _, enqueue, _, github_app = harness
    await deliver(
        app, {"action": "deleted", "installation": {"id": 200}}, "installation"
    )
    github_app.invalidate.assert_called_once_with(200)
    enqueue.assert_not_awaited()


async def request_review(app, owner="owner"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/request/github",
            json={
                "owner": owner,
                "repo": "repo",
                "pr_number": 15,
                "user_id": 7,
                "user_name": "author",
            },
            headers={"Authorization": f"Bearer {settings.external_api_key}"},
        )


@pytest.mark.asyncio
async def test_manual_request_requires_enrollment(harness):
    app, _, enqueue, _, github_app = harness
    assert (await request_review(app, owner="other")).status_code == 404
    github_app.repo_client.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_request_requires_app_installation(harness):
    app, _, enqueue, _, github_app = harness
    missing = response({"message": "Not Found"}, 404)
    github_app.repo_client.side_effect = httpx.HTTPStatusError(
        "missing", request=missing.request, response=missing
    )
    assert (await request_review(app)).status_code == 409
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_request_queues_enrolled_repository(harness):
    app, _, enqueue, api, github_app = harness
    assert (await request_review(app)).status_code == 200
    github_app.repo_client.assert_awaited_once_with("owner/repo")
    assert enqueue.await_args.args[1:] == (api, "owner/repo", 15, 7, "author")


@pytest.mark.asyncio
async def test_duplicate_request_repairs_enqueue_failure(monkeypatch):
    db = AsyncMock()
    existing = review()
    db.scalar.side_effect = [None, existing]
    api = SimpleNamespace(request=AsyncMock(return_value=response(pull_request())))
    enqueue = AsyncMock()
    monkeypatch.setattr(github_reviews, "create_review_task", enqueue)
    result = await github_reviews.request_github_review(
        db, api, "owner/repo", 15, 7, "author"
    )
    assert result is existing
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["deduplicate"] is True


@pytest.mark.asyncio
async def test_drafts_are_not_queued(monkeypatch):
    api = SimpleNamespace(
        request=AsyncMock(return_value=response(pull_request(draft=True)))
    )
    db = AsyncMock()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await github_reviews.request_github_review(
            db, api, "owner/repo", 15, 7, "author"
        )
    assert error.value.status_code == 409
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_reconciles_success_after_worker_crash(monkeypatch):
    published = {
        "id": 30,
        "body": "<!-- reviewhelper:10 -->",
        "user": {"login": "reviewhelper[bot]"},
        "commit_id": SHA,
    }
    api = SimpleNamespace(
        request=AsyncMock(
            side_effect=[
                response([published]),
                response([{"id": 40, "path": "f.rs", "line": 1, "body": "Fix this"}]),
            ]
        )
    )
    app = SimpleNamespace(
        repo_client=AsyncMock(return_value=api),
        bot_login=AsyncMock(return_value="reviewhelper[bot]"),
    )
    monkeypatch.setattr(review_processor, "get_github_app", lambda: app)
    check = AsyncMock(side_effect=AssertionError("Already published"))
    monkeypatch.setattr(review_processor, "current_pr", check)
    comment = SimpleNamespace(
        id=50,
        platform_comment_id=None,
        file_path="f.rs",
        line_start=1,
        line_end=1,
        on_new=True,
        content="Fix this",
    )
    result = [
        item
        async for item in review_processor.submit_review_to_platform(
            review(), [comment]
        )
    ]
    assert result == [(comment, 40)]
    assert all(call.args[0] == "GET" for call in api.request.await_args_list)


@pytest.mark.asyncio
async def test_publication_skips_superseded_pr(monkeypatch):
    api = SimpleNamespace(request=AsyncMock(return_value=response([])))
    app = SimpleNamespace(
        repo_client=AsyncMock(return_value=api),
        bot_login=AsyncMock(return_value="reviewhelper[bot]"),
    )
    monkeypatch.setattr(review_processor, "get_github_app", lambda: app)
    monkeypatch.setattr(review_processor, "current_pr", AsyncMock(return_value=None))
    with pytest.raises(review_processor.SupersededReviewError):
        _ = [
            item
            async for item in review_processor.submit_review_to_platform(review(), [])
        ]
    api.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_publication_posts_one_review_with_final_diff_coordinates(monkeypatch):
    api = SimpleNamespace(
        request=AsyncMock(
            side_effect=[
                response([]),
                response(DIFF),
                response({"id": 30}),
                response([]),
            ]
        )
    )
    app = SimpleNamespace(
        repo_client=AsyncMock(return_value=api),
        bot_login=AsyncMock(return_value="reviewhelper[bot]"),
    )
    monkeypatch.setattr(review_processor, "get_github_app", lambda: app)
    monkeypatch.setattr(
        review_processor, "current_pr", AsyncMock(return_value=pull_request())
    )
    valid = SimpleNamespace(
        id=50,
        platform_comment_id=None,
        file_path="f.rs",
        line_start=1,
        line_end=1,
        on_new=True,
        content="Fix this",
    )
    invalid = SimpleNamespace(
        id=51,
        platform_comment_id=None,
        file_path="f.rs",
        line_start=99,
        line_end=99,
        on_new=True,
        content="Invalid line",
    )
    _ = [
        item
        async for item in review_processor.submit_review_to_platform(
            review(), [valid, invalid]
        )
    ]
    post = api.request.await_args_list[2]
    assert post.args[0] == "POST"
    assert post.kwargs["json"]["commit_id"] == SHA
    assert post.kwargs["json"]["comments"] == [
        {"path": "f.rs", "line": 1, "side": "RIGHT", "body": "Fix this"}
    ]


@pytest.mark.asyncio
async def test_transport_does_not_retry_uncertain_writes(monkeypatch):
    http = SimpleNamespace(
        request=AsyncMock(side_effect=httpx.ReadTimeout("uncertain write"))
    )
    monkeypatch.setattr(github, "get_http_client", lambda: http)
    app = SimpleNamespace(token=AsyncMock(return_value="dummy"))
    with pytest.raises(httpx.ReadTimeout):
        await github.GitHubClient(app, 200).request(
            "POST", "repos/owner/repo/pulls/15/reviews", json={}
        )
    http.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_named_tasks_accept_duplicate_delivery(monkeypatch):
    from google.api_core.exceptions import AlreadyExists

    client = MagicMock()
    client.queue_path.return_value = "projects/test/locations/test/queues/test"
    client.create_task = AsyncMock(side_effect=AlreadyExists("duplicate"))
    monkeypatch.setattr(tasks, "_get_tasks_client", lambda: client)
    await tasks.create_review_task(10, deduplicate=True)
    assert client.create_task.await_args.kwargs["task"].name.endswith(
        "/tasks/review-10"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sha,enabled,eligible",
    [(SHA, True, True), ("c" * 40, True, False), (SHA, False, False)],
)
async def test_current_pr_requires_enrollment_and_matching_head(
    monkeypatch, sha, enabled, eligible
):
    if not enabled:
        monkeypatch.setattr(settings, "github_repositories", [])
    pr = pull_request(head={"sha": sha})
    api = SimpleNamespace(request=AsyncMock(return_value=response(pr)))
    monkeypatch.setattr(
        github_reviews,
        "get_github_app",
        lambda: SimpleNamespace(repo_client=AsyncMock(return_value=api)),
    )
    assert (await github_reviews.current_pr(review()) is not None) is eligible
    if not enabled:
        api.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limited_eligibility_check_is_retryable(monkeypatch):
    limited = response({"message": "rate limit exceeded"}, 403)
    api = SimpleNamespace(
        request=AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "limited", request=limited.request, response=limited
            )
        )
    )
    monkeypatch.setattr(
        github_reviews,
        "get_github_app",
        lambda: SimpleNamespace(repo_client=AsyncMock(return_value=api)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await github_reviews.current_pr(review())


@pytest.mark.asyncio
async def test_installation_tokens_are_cached_and_refreshed_before_expiry(monkeypatch):
    app = github.GitHubApp()
    monkeypatch.setattr(app, "jwt", lambda: "dummy-jwt")
    clock = [1000]
    monkeypatch.setattr(github.time, "time", lambda: clock[0])
    transport = SimpleNamespace(
        post=AsyncMock(
            side_effect=[
                response({"token": "dummy-1", "expires_at": "1970-01-01T00:20:00Z"}),
                response({"token": "dummy-2", "expires_at": "1970-01-01T00:40:00Z"}),
            ]
        )
    )
    monkeypatch.setattr(github, "get_http_client", lambda: transport)
    assert await app.token(200) == "dummy-1"
    assert await app.token(200) == "dummy-1"
    transport.post.assert_awaited_once()
    clock[0] = 1150
    assert await app.token(200) == "dummy-2"
    assert transport.post.await_count == 2


@pytest.mark.asyncio
async def test_installation_lookup_is_cached_until_uninstall(monkeypatch):
    app = github.GitHubApp()
    lookup = AsyncMock(side_effect=[{"id": 200}, {"id": 300}])
    monkeypatch.setattr(app, "app_request", lookup)
    assert await app.installation_id("owner/repo") == 200
    assert await app.installation_id("owner/repo") == 200
    app.invalidate(200)
    assert await app.installation_id("owner/repo") == 300
    assert lookup.await_count == 2


def test_app_jwt_is_signed_and_short_lived(monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_app_private_key", pem.replace("\n", "\\n"))
    claims = jwt.decode(
        github.GitHubApp().jwt(), key.public_key(), algorithms=["RS256"]
    )
    assert claims["iss"] == "123"
    assert claims["exp"] - claims["iat"] == 600
    assert claims["iat"] <= time.time() < claims["exp"]


@pytest.mark.asyncio
async def test_base_branch_movement_does_not_supersede_review(monkeypatch):
    api = SimpleNamespace(
        request=AsyncMock(return_value=response(pull_request(base={"sha": "c" * 40})))
    )
    monkeypatch.setattr(
        github_reviews,
        "get_github_app",
        lambda: SimpleNamespace(repo_client=AsyncMock(return_value=api)),
    )
    assert await github_reviews.current_pr(review()) is not None


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Failed or superseded rows for the same snapshot swallow new requests",
)
@pytest.mark.asyncio
async def test_new_request_retries_failed_or_superseded_snapshot(monkeypatch):
    db = AsyncMock()
    existing = review()
    existing.status = ReviewStatus.FAILED
    db.scalar.side_effect = [None, existing]
    api = SimpleNamespace(request=AsyncMock(return_value=response(pull_request())))
    enqueue = AsyncMock()
    monkeypatch.setattr(github_reviews, "create_review_task", enqueue)
    await github_reviews.request_github_review(db, api, "owner/repo", 15, 7, "author")
    enqueue.assert_awaited_once()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="GET retries wait out rate limits inside GitHub's 10s webhook timeout",
)
@pytest.mark.asyncio
async def test_webhook_does_not_wait_out_rate_limits(harness, monkeypatch):
    app, _, _, _, github_app = harness
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(github, "asyncio", SimpleNamespace(sleep=sleep))
    limited = httpx.Response(
        429,
        headers={"Retry-After": "60"},
        request=httpx.Request("GET", "https://api.github.com/test"),
    )
    http = SimpleNamespace(
        request=AsyncMock(
            side_effect=[
                limited,
                response(pull_request()),
                response({"permission": "write"}),
            ]
        )
    )
    monkeypatch.setattr(github, "get_http_client", lambda: http)
    tokens = SimpleNamespace(token=AsyncMock(return_value="dummy"))
    github_app.client = lambda installation: github.GitHubClient(tokens, installation)
    data = payload("issue_comment", "created")
    data["sender"]["id"] = 9
    await deliver(app, data, "issue_comment")
    assert sum(sleeps) < 10


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Multi-line and context-line findings are dropped before publication",
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "line_start,line_end",
    [(2, 3), (1, 1)],
    ids=["multi-line", "context-line"],
)
async def test_publication_keeps_valid_review_coordinates(
    monkeypatch, line_start, line_end
):
    diff = "--- a/f.rs\n+++ b/f.rs\n@@ -1,3 +1,4 @@\n ctx\n-old\n+new1\n+new2\n tail\n"
    api = SimpleNamespace(
        request=AsyncMock(
            side_effect=[
                response([]),
                response(diff),
                response({"id": 30}),
                response([]),
            ]
        )
    )
    app = SimpleNamespace(
        repo_client=AsyncMock(return_value=api),
        bot_login=AsyncMock(return_value="reviewhelper[bot]"),
    )
    monkeypatch.setattr(review_processor, "get_github_app", lambda: app)
    monkeypatch.setattr(
        review_processor, "current_pr", AsyncMock(return_value=pull_request())
    )
    comment = SimpleNamespace(
        id=50,
        platform_comment_id=None,
        file_path="f.rs",
        line_start=line_start,
        line_end=line_end,
        on_new=True,
        content="Fix this",
    )
    _ = [
        item
        async for item in review_processor.submit_review_to_platform(
            review(), [comment]
        )
    ]
    post = api.request.await_args_list[2]
    assert len(post.kwargs["json"]["comments"]) == 1
