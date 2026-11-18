"""Tests for the GitHub action handlers: comments, reviews and pull requests.

Drive the real githubkit client against a fake api.github.com (an
``httpx.MockTransport``), so these check the calls the handlers actually make.
The commits artifact ``github.submit_patch`` reads is built agent-side (see
hackbot-runtime's test_changes.py ``build_github_commits`` tests).
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.action_handlers import github_handler
from app.action_handlers.base import ApplyContext
from app.action_handlers.registry import PATCH_ACTION_TYPES, get_handler
from app.actions_applier import _apply_pending_rows
from app.agents import AGENT_REGISTRY
from app.config import GitHubAppSettings
from app.github import GitHubApp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

INSTALLATION_ID = 99
CTX = ApplyContext(
    run_id="run-1", agent="test-agent", download_artifact=None, hackbot_client=None
)


async def test_general_and_inline_actions_send_one_github_review(fake):
    rows = [
        (
            SimpleNamespace(
                id=1,
                idx=0,
                type="github.add_comment",
                status="pending",
                ref=None,
                params={"repo": "o/r", "pull_number": 7, "text": "Overall feedback."},
            ),
            [],
        ),
        (
            SimpleNamespace(
                id=2,
                idx=1,
                type="github.add_inline_comments",
                status="pending",
                ref=None,
                params={
                    "repo": "o/r",
                    "pull_number": 7,
                    "comments": [{"path": "a.py", "line": 3, "body": "Check this."}],
                    "summary": "One thing.",
                },
            ),
            [],
        ),
    ]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: [1, 2])
    await _apply_pending_rows(
        db,
        SimpleNamespace(run_id="run-1", agent="test-agent", summary={}),
        rows,
        None,
    )
    posts = [
        r
        for r in fake.requests
        if r.method == "POST" and r.url.path.startswith("/repos/")
    ]
    assert len(posts) == 1
    assert posts[0].url.path == "/repos/o/r/pulls/7/reviews"
    assert json.loads(posts[0].content) == {
        "event": "COMMENT",
        "body": "Overall feedback.\n\nOne thing.",
        "comments": [
            {"path": "a.py", "line": 3, "side": "RIGHT", "body": "Check this."}
        ],
    }
    assert all(row.status == "applied" for row, _ in rows)


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
        GitHubAppSettings(app_id=123, private_key=pem, webhook_secret="test-secret"),
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


# --- github.submit_patch ---------------------------------------------------

OWNER = "owner"
REPO = "repo"
RUN_ID = "run-1"
BRANCH = "hackbot/run-1"
PR_URL = f"https://github.com/{OWNER}/{REPO}/pull/7"

_SUBMISSION = {
    "repo_url": f"https://github.com/{OWNER}/{REPO}.git",
    "base_commit": "base",
    "commits": [
        {
            "message": "First\n\nBody.",
            "author": {
                "name": "Dev",
                "email": "dev@example.com",
                "date": "2026-11-09T10:00:00+00:00",
            },
            "changes": [
                {"path": "a.txt", "mode": "100644", "content": "YQo="},
                {"path": "old.txt", "mode": "100644", "deleted": True},
            ],
        },
        {
            "message": "Second",
            "author": {
                "name": "Hackbot",
                "email": "hackbot@mozilla.tld",
                "date": "2026-11-09T11:00:00+00:00",
            },
            "changes": [
                {"path": "run.sh", "mode": "100755", "content": "IyEK"},
                {"path": "third_party/lib", "mode": "160000", "sha": "c0ffee"},
            ],
        },
    ],
}


class _FakeGitData:
    """Stands in for api.github.com's Git Data and pulls endpoints.

    Numbers every object it creates.
    """

    def __init__(self, *, branch_exists=False, pull_request_exists=False):
        self.branch_exists = branch_exists
        self.pull_request_exists = pull_request_exists
        self.requests: list[tuple[str, str, dict | None]] = []
        self.created = 0

    def calls(self, method: str, path: str) -> list[dict | None]:
        return [body for m, p, body in self.requests if (m, p) == (method, path)]

    def _new_sha(self, kind: str) -> str:
        self.created += 1
        return f"{kind}{self.created}"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        # Undecoded, to catch a ref path whose slashes get escaped.
        path = request.url.raw_path.decode().partition("?")[0]
        body = json.loads(request.content) if request.content else None
        self.requests.append((method, path, body))
        repo = f"/repos/{OWNER}/{REPO}"

        if path.endswith("/access_tokens"):
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            return httpx.Response(
                201,
                json={
                    "token": "ghs_test",
                    "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        match method, path.removeprefix(repo):
            case "GET", "/installation":
                return httpx.Response(200, json={"id": INSTALLATION_ID})
            case "GET", "":
                return httpx.Response(200, json={"default_branch": "main"})
            case "GET", "/git/commits/base":
                return httpx.Response(
                    200, json={"sha": "base", "tree": {"sha": "basetree"}}
                )
            case "POST", "/git/blobs":
                return httpx.Response(201, json={"sha": self._new_sha("blob")})
            case "POST", "/git/trees":
                return httpx.Response(201, json={"sha": self._new_sha("tree")})
            case "POST", "/git/commits":
                return httpx.Response(201, json={"sha": self._new_sha("commit")})
            case "POST", "/git/refs":
                if self.branch_exists:
                    return httpx.Response(
                        422, json={"message": "Reference already exists"}
                    )
                return httpx.Response(201, json={"ref": body["ref"]})
            case "PATCH", "/git/refs/heads/hackbot/run-1":
                return httpx.Response(200, json={"ref": f"refs/heads/{BRANCH}"})
            case "POST", "/pulls":
                if self.pull_request_exists:
                    return httpx.Response(
                        422, json={"message": "A pull request already exists"}
                    )
                return httpx.Response(201, json={"number": 7, "html_url": PR_URL})
            case "GET", "/pulls":
                return httpx.Response(200, json=[{"number": 7, "html_url": PR_URL}])
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})


@pytest.fixture
def agent_registry(monkeypatch):
    """Registers ``test-agent`` with ``owner/repo`` as its GitHub repository."""

    def register(github_repository: str | None = f"{OWNER}/{REPO}") -> None:
        spec = replace(
            AGENT_REGISTRY["bug-fix"],
            name="test-agent",
            github_repository=github_repository,
        )
        monkeypatch.setattr(github_handler, "AGENT_REGISTRY", {"test-agent": spec})

    register()
    return register


@pytest.fixture
def fake_github(monkeypatch, pem, agent_registry):
    def install(**kwargs) -> _FakeGitData:
        fake = _FakeGitData(**kwargs)
        app = GitHubApp(
            GitHubAppSettings(
                app_id=123, private_key=pem, webhook_secret="test-webhook-secret"
            ),
            async_transport=httpx.MockTransport(fake),
            http_cache=False,
            auto_retry=False,
        )
        monkeypatch.setattr(github_handler, "_app", lambda: app)
        return fake

    return install


def _ctx(submission=_SUBMISSION):
    async def download(key, max_bytes=None):
        assert key == "changes/github_commits.json"
        if submission is None:
            raise FileNotFoundError(key)
        return json.dumps(submission).encode()

    return ApplyContext(
        run_id=RUN_ID,
        agent="test-agent",
        download_artifact=download,
        hackbot_client=object(),
    )


_PARAMS = {"title": "  Fix the thing ", "body": "What and why."}


def test_submit_patch_is_registered_as_a_patch_action():
    assert isinstance(
        get_handler("github.submit_patch"), github_handler.SubmitPatchHandler
    )
    assert "github.submit_patch" in PATCH_ACTION_TYPES


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/mozilla/bugbug.git", ("mozilla", "bugbug")),
        ("https://github.com/mozilla/bugbug", ("mozilla", "bugbug")),
        (
            "https://github.com/mozilla-firefox/firefox.git/",
            ("mozilla-firefox", "firefox"),
        ),
        ("git@github.com:mozilla/bugbug.git", ("mozilla", "bugbug")),
        ("ssh://git@github.com/mozilla/web.compat.git", ("mozilla", "web.compat")),
        ("https://hg.mozilla.org/mozilla-central", None),
        ("https://github.com.evil.example/mozilla/bugbug", None),
        ("https://github.com/mozilla/bugbug/tree/main", None),
    ],
)
def test_parse_repo_url(url, expected):
    assert github_handler.parse_repo_url(url) == expected


async def test_submit_patch_recreates_the_commits_and_opens_a_draft_pr(fake_github):
    fake = fake_github()

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx())

    assert result.status == "applied", result.error
    assert result.result == {
        "pull_request_number": 7,
        "url": PR_URL,
        "repository": f"{OWNER}/{REPO}",
        "branch": BRANCH,
    }

    repo = f"/repos/{OWNER}/{REPO}"
    # One blob per changed file with content; deletions and submodules need none.
    assert fake.calls("POST", f"{repo}/git/blobs") == [
        {"content": "YQo=", "encoding": "base64"},
        {"content": "IyEK", "encoding": "base64"},
    ]
    first_tree, second_tree = fake.calls("POST", f"{repo}/git/trees")
    assert first_tree == {
        "base_tree": "basetree",
        "tree": [
            {"path": "a.txt", "mode": "100644", "type": "blob", "sha": "blob1"},
            {"path": "old.txt", "mode": "100644", "type": "blob", "sha": None},
        ],
    }
    # Each commit's tree builds on the one before it.
    assert second_tree == {
        "base_tree": "tree2",
        "tree": [
            {"path": "run.sh", "mode": "100755", "type": "blob", "sha": "blob4"},
            {
                "path": "third_party/lib",
                "mode": "160000",
                "type": "commit",
                "sha": "c0ffee",
            },
        ],
    }
    first_commit, second_commit = fake.calls("POST", f"{repo}/git/commits")
    assert first_commit == {
        "message": "First\n\nBody.",
        "tree": "tree2",
        "parents": ["base"],
        # githubkit writes a UTC offset as "Z"; any other offset is kept.
        "author": {
            "name": "Dev",
            "email": "dev@example.com",
            "date": "2026-11-09T10:00:00Z",
        },
    }
    assert second_commit["parents"] == ["commit3"]
    assert second_commit["tree"] == "tree5"
    # No committer: GitHub commits (and signs) as the App.
    assert "committer" not in second_commit

    assert fake.calls("POST", f"{repo}/git/refs") == [
        {"ref": f"refs/heads/{BRANCH}", "sha": "commit6"}
    ]
    assert fake.calls("POST", f"{repo}/pulls") == [
        {
            "title": "Fix the thing",
            "head": BRANCH,
            "base": "main",
            "draft": True,
            "body": "What and why.",
        }
    ]
    # The token is narrowed to the one repository, with write access.
    (token_request,) = fake.calls(
        "POST", f"/app/installations/{INSTALLATION_ID}/access_tokens"
    )
    assert token_request["repositories"] == [REPO]
    assert token_request["permissions"] == {
        "contents": "write",
        "pull_requests": "write",
    }


async def test_submit_patch_omits_an_empty_body(fake_github):
    fake = fake_github()

    result = await github_handler.SubmitPatchHandler().apply(
        {"title": "Fix", "body": None}, _ctx()
    )

    assert result.status == "applied", result.error
    (pull_request,) = fake.calls("POST", f"/repos/{OWNER}/{REPO}/pulls")
    assert "body" not in pull_request


async def test_retry_reuses_the_runs_branch_and_open_pull_request(fake_github):
    # An earlier attempt pushed the branch and opened the PR, then failed
    # before its result was recorded.
    fake = fake_github(branch_exists=True, pull_request_exists=True)

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx())

    assert result.status == "applied", result.error
    assert result.result["url"] == PR_URL
    repo = f"/repos/{OWNER}/{REPO}"
    assert fake.calls("PATCH", f"{repo}/git/refs/heads/hackbot/run-1") == [
        {"sha": "commit6", "force": True}
    ]
    listing = [r for r in fake.requests if r[:2] == ("GET", f"{repo}/pulls")]
    assert len(listing) == 1


async def test_submit_patch_fails_for_a_repository_not_on_github(fake_github):
    fake = fake_github()
    submission = {**_SUBMISSION, "repo_url": "https://hg.mozilla.org/try"}

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx(submission))

    assert result.status == "failed"
    assert "not on GitHub" in result.error
    assert fake.requests == []


async def test_submit_patch_targets_the_configured_repository(fake_github):
    # GitHub names are case-insensitive, so a repo_url that differs only in
    # case still matches. The calls use the configured name, not the artifact's.
    fake = fake_github()
    submission = {**_SUBMISSION, "repo_url": "https://github.com/OWNER/Repo.git"}

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx(submission))

    assert result.status == "applied", result.error
    assert result.result["repository"] == f"{OWNER}/{REPO}"
    assert fake.calls("POST", f"/repos/{OWNER}/{REPO}/pulls")


async def test_submit_patch_fails_for_an_agent_without_a_github_repository(
    fake_github, agent_registry
):
    fake = fake_github()
    agent_registry(github_repository=None)

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx())

    assert result.status == "failed"
    assert "not configured to open pull requests" in result.error
    assert fake.requests == []


async def test_submit_patch_refuses_a_repository_other_than_the_configured_one(
    fake_github,
):
    # The artifact is agent-written: it must not pick where hackbot writes.
    fake = fake_github()
    submission = {**_SUBMISSION, "repo_url": "https://github.com/other/repo.git"}

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx(submission))

    assert result.status == "failed"
    assert "may only open pull requests on owner/repo" in result.error
    # Refused before any credentials are minted.
    assert fake.requests == []


async def test_submit_patch_refuses_changes_to_workflow_files(fake_github):
    fake = fake_github()
    commit = {
        **_SUBMISSION["commits"][0],
        "changes": [
            {"path": ".github/CODEOWNERS", "mode": "100644", "content": "YQo="},
            {"path": ".github/workflows/ci.yml", "mode": "100644", "content": "YQo="},
        ],
    }
    deletion = {
        **_SUBMISSION["commits"][1],
        "changes": [
            {"path": ".github/workflows/old.yml", "mode": "100644", "deleted": True}
        ],
    }
    submission = {**_SUBMISSION, "commits": [commit, deletion]}

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx(submission))

    assert result.status == "failed"
    # Every workflow path is named, deletions included; other .github files
    # are fine.
    assert result.error.endswith(".github/workflows/ci.yml, .github/workflows/old.yml")
    assert "CODEOWNERS" not in result.error
    assert fake.requests == []


async def test_submit_patch_fails_without_the_artifact(fake_github):
    fake = fake_github()

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx(None))

    assert result.status == "failed"
    assert "GitHub commits artifact" in result.error
    assert fake.requests == []


async def test_submit_patch_fails_when_github_rejects_a_call(fake_github):
    fake = fake_github()
    submission = {**_SUBMISSION, "base_commit": "unknown"}

    result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx(submission))

    assert result.status == "failed"
    assert result.error.startswith("GitHub API error 404")
    # Nothing is pushed on top of a base GitHub doesn't have.
    assert not fake.calls("POST", f"/repos/{OWNER}/{REPO}/git/refs")


async def test_submit_patch_fails_when_the_github_app_is_not_configured(
    monkeypatch, agent_registry
):
    monkeypatch.setattr(github_handler.settings, "github", None)
    github_handler._app.cache_clear()
    try:
        result = await github_handler.SubmitPatchHandler().apply(_PARAMS, _ctx())
    finally:
        github_handler._app.cache_clear()

    assert result.status == "failed"
    assert result.error.startswith("The GitHub App is not configured")
