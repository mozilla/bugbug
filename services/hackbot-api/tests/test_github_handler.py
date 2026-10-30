"""Tests for the GitHub action handlers (PR comments and inline reviews)."""

import json
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest
from app.action_handlers import github_handler
from app.action_handlers.base import ApplyContext
from app.action_handlers.registry import get_handler
from app.config import GitHubAppSettings
from app.github import GitHubApp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

INSTALLATION_ID = 99
CTX = ApplyContext(run_id="run-1", agent="test-agent", download_artifact=None)


@pytest.fixture(scope="module")
def pem() -> str:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        .decode()
    )


class _FakeGitHub:
    """Stands in for api.github.com; ``fail`` maps a path to an error response."""

    def __init__(self, fail: dict[str, httpx.Response] | None = None):
        self.fail = fail or {}
        self.requests: list[httpx.Request] = []

    def request_to(self, path: str) -> httpx.Request:
        return next(r for r in self.requests if r.url.path == path)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.fail:
            return self.fail[path]
        if path.endswith("/installation"):
            return httpx.Response(200, json={"id": INSTALLATION_ID})
        if path.endswith("/access_tokens"):
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            return httpx.Response(
                201,
                json={
                    "token": "ghs_test",
                    "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        if path.endswith("/comments"):
            return httpx.Response(
                201, json={"html_url": "https://github.com/o/r/pull/7#issuecomment-1"}
            )
        if path.endswith("/reviews"):
            return httpx.Response(
                200,
                json={"html_url": "https://github.com/o/r/pull/7#pullrequestreview-1"},
            )
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def fake(monkeypatch, pem):
    fake = _FakeGitHub()
    monkeypatch.setattr(
        github_handler.settings,
        "github",
        GitHubAppSettings(app_id=123, private_key=pem),
    )
    monkeypatch.setattr(
        github_handler,
        "GitHubApp",
        partial(GitHubApp, async_transport=httpx.MockTransport(fake), http_cache=False),
    )
    github_handler._app.cache_clear()
    try:
        yield fake
    finally:
        github_handler._app.cache_clear()


async def test_add_comment_posts_with_a_comment_only_token(fake):
    result = await get_handler("github.add_comment").apply(
        {"repo": "o/r", "pull_number": 7, "text": "Answering your question."}, CTX
    )

    assert result.status == "applied"
    assert result.result == {
        "repo": "o/r",
        "pull_number": 7,
        "comment_url": "https://github.com/o/r/pull/7#issuecomment-1",
    }
    lookup = fake.request_to("/repos/o/r/installation")
    assert lookup.headers["Authorization"].startswith("Bearer ")
    mint = fake.request_to(f"/app/installations/{INSTALLATION_ID}/access_tokens")
    assert json.loads(mint.content) == {
        "permissions": {"pull_requests": "write"},
        "repositories": ["r"],
    }
    comment = fake.request_to("/repos/o/r/issues/7/comments")
    assert comment.headers["Authorization"] == "token ghs_test"
    assert json.loads(comment.content) == {"body": "Answering your question."}


async def test_add_inline_comments_posts_one_review(fake):
    result = await get_handler("github.add_inline_comments").apply(
        {
            "repo": "o/r",
            "pull_number": 7,
            "comments": [
                {"path": "a.py", "line": 3, "body": "Off by one?"},
                {"path": "b.py", "line": 12, "start_line": 10, "body": "Extract."},
            ],
            "summary": "Two things.",
        },
        CTX,
    )

    assert result.status == "applied"
    assert result.result["review_url"].endswith("#pullrequestreview-1")
    review = fake.request_to("/repos/o/r/pulls/7/reviews")
    assert json.loads(review.content) == {
        "event": "COMMENT",
        "body": "Two things.",
        "comments": [
            {"path": "a.py", "line": 3, "side": "RIGHT", "body": "Off by one?"},
            {
                "path": "b.py",
                "line": 12,
                "side": "RIGHT",
                "start_line": 10,
                "start_side": "RIGHT",
                "body": "Extract.",
            },
        ],
    }


async def test_add_inline_comments_without_a_summary_has_no_review_body(fake):
    await github_handler.AddInlineCommentsHandler().apply(
        {
            "repo": "o/r",
            "pull_number": 7,
            "comments": [{"path": "a.py", "line": 3, "body": "Nit."}],
        },
        CTX,
    )
    review = fake.request_to("/repos/o/r/pulls/7/reviews")
    assert "body" not in json.loads(review.content)


async def test_github_rejecting_a_review_fails_with_its_reason(fake):
    fake.fail["/repos/o/r/pulls/7/reviews"] = httpx.Response(
        422,
        json={
            "message": "Unprocessable Entity",
            "errors": ["Line could not be resolved"],
        },
    )

    result = await github_handler.AddInlineCommentsHandler().apply(
        {
            "repo": "o/r",
            "pull_number": 7,
            "comments": [{"path": "a.py", "line": 50, "body": "x"}],
        },
        CTX,
    )

    assert result.status == "failed"
    assert result.error == (
        "GitHub API error 422: Unprocessable Entity (Line could not be resolved)"
    )


async def test_a_repo_without_the_app_installed_fails(fake):
    fake.fail["/repos/o/r/installation"] = httpx.Response(
        404, json={"message": "Not Found"}
    )

    result = await github_handler.AddCommentHandler().apply(
        {"repo": "o/r", "pull_number": 7, "text": "x"}, CTX
    )

    assert result.status == "failed"
    assert result.error == "GitHub API error 404: Not Found"
    assert not any(r.url.path.endswith("/comments") for r in fake.requests)


@pytest.mark.parametrize(
    ("action_type", "params"),
    [
        ("github.add_comment", {"repo": "o/r", "pull_number": 7, "text": "x"}),
        (
            "github.add_inline_comments",
            {
                "repo": "o/r",
                "pull_number": 7,
                "comments": [{"path": "a.py", "line": 3, "body": "x"}],
            },
        ),
    ],
)
async def test_an_unconfigured_app_fails_without_breaking_the_service(
    monkeypatch, action_type, params
):
    monkeypatch.setattr(github_handler.settings, "github", None)
    github_handler._app.cache_clear()
    try:
        result = await get_handler(action_type).apply(params, CTX)
    finally:
        github_handler._app.cache_clear()

    assert result.status == "failed"
    assert result.error.startswith("The GitHub App is not configured")
    assert "GITHUB_APP_ID" in result.error
    assert "GITHUB_PRIVATE_KEY" in result.error
