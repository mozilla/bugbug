"""Tests for checking the source tree out at a GitHub pull request.

Nothing is faked. A local repository stands in for GitHub, serving each PR under
``refs/pull/<n>/head`` the way GitHub does, and the checkout is the real shallow
clone ``HackbotContext.prepare_repo`` makes of it.
"""

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from hackbot_runtime import HackbotContext, checkout_pull_request, pull_request
from hackbot_runtime.config import HackbotConfig, SourceConfig
from hackbot_runtime.source import ensure_source_repo

PR = 1


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str, path: str, content: str) -> str:
    (repo / path).write_text(content)
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=Author",
        "-c",
        "user.email=author@example.com",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _append_commits(
    repo: Path, ref: str, parent: str, label: str, path: str, count: int
) -> str:
    """Point ``ref`` at ``count`` new commits on top of ``parent``; return the last.

    Commit ``i`` is titled "<label> <i>" and leaves ``path`` holding one line per
    commit so far. Written with a single ``git fast-import`` rather than a few git
    calls per commit, which keeps the tests that need long histories quick.
    """
    if count == 0:
        return parent
    stream = []
    for i in range(1, count + 1):
        message = f"{label} {i}\n"
        content = "".join(f"{label} {j}\n" for j in range(1, i + 1))
        stream += [
            f"commit {ref}",
            f"committer Author <author@example.com> {1_700_000_000 + i} +0000",
            f"data {len(message)}",
            message,
            *([f"from {parent}"] if i == 1 else []),
            f"M 644 inline {path}",
            f"data {len(content)}",
            content,
        ]
    subprocess.run(
        ["git", "-C", str(repo), "fast-import", "--quiet"],
        input="\n".join(stream),
        check=True,
        text=True,
    )
    return _git(repo, "rev-parse", ref)


class _Origin(NamedTuple):
    path: Path
    fork_point: str
    head: str
    base: str


def _origin(
    tmp_path: Path, *, pr_commits: int = 3, landed_since: int = 5, history: int = 0
) -> _Origin:
    """A repo serving PR #1, branched off `main` with more landed on `main` since.

    ``history`` adds commits below the one the PR branched from. The PR's commits
    are only reachable from refs/pull, with no branch of their own, as for a PR
    from a fork.
    """
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    main = "refs/heads/main"
    initial = _commit(repo, "Initial commit", "file.txt", "base\n")
    fork_point = _append_commits(repo, main, initial, "Older", "file.txt", history)
    head = _append_commits(
        repo, f"refs/pull/{PR}/head", fork_point, "PR commit", "pr.txt", pr_commits
    )
    base = _append_commits(repo, main, fork_point, "Landed", "landed.txt", landed_since)
    # fast-import moves refs only; bring main's working tree along with it.
    _git(repo, "reset", "-q", "--hard")
    return _Origin(repo, fork_point, head, base)


def _ctx(tmp_path: Path, origin: _Origin) -> HackbotContext:
    ctx = HackbotContext(run_id="local-test", artifacts_dir=tmp_path / "artifacts")
    ctx._config = HackbotConfig(
        source=SourceConfig(
            repo_url=origin.path.as_uri(), checkout_path=tmp_path / "checkout"
        )
    )
    return ctx


@pytest.fixture(autouse=True)
def _no_source_overrides(monkeypatch):
    # Either would point prepare_repo somewhere other than the test's origin.
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.delenv("SOURCE_REF", raising=False)


async def test_the_checkout_is_the_pr_head_with_nothing_uncommitted(tmp_path):
    origin = _origin(tmp_path)
    ctx = _ctx(tmp_path, origin)

    await checkout_pull_request(ctx, PR, "main", head_sha=origin.head)

    assert _git(ctx.repo_path, "rev-parse", "HEAD") == origin.head
    assert (ctx.repo_path / "pr.txt").read_text() == (
        "PR commit 1\nPR commit 2\nPR commit 3\n"
    )
    assert _git(ctx.repo_path, "status", "--porcelain") == ""


@pytest.mark.parametrize("base", ["main", "sha"], ids=["base-branch", "base-sha"])
async def test_the_base_gives_the_same_change_github_shows(tmp_path, base):
    origin = _origin(tmp_path)
    base = origin.base if base == "sha" else base
    ctx = _ctx(tmp_path, origin)

    merge_base = await checkout_pull_request(ctx, PR, base)

    assert merge_base == origin.fork_point
    # Only the PR's own change, not everything that landed on main since.
    assert _git(ctx.repo_path, "diff", "--name-only", merge_base, "HEAD") == "pr.txt"
    # One commit per PR commit, so the agent can read the PR's history.
    assert _git(ctx.repo_path, "log", "--format=%s", f"{merge_base}..HEAD") == (
        "PR commit 3\nPR commit 2\nPR commit 1"
    )


async def test_a_pr_far_from_its_base_still_finds_where_it_branched(tmp_path):
    # Both distances exceed the first round of fetched history, on both sides.
    origin = _origin(tmp_path, pr_commits=20, landed_since=40, history=100)
    ctx = _ctx(tmp_path, origin)

    merge_base = await checkout_pull_request(ctx, PR, "main")

    assert merge_base == origin.fork_point
    # Only as much history as the search needed, not the whole repository.
    assert _git(ctx.repo_path, "rev-parse", "--is-shallow-repository") == "true"


async def test_the_prs_commits_are_not_counted_as_the_agents_work(tmp_path):
    # What the run publishes, and what a later push to the PR's branch would
    # add, is only what the agent changes on top of the PR.
    origin = _origin(tmp_path)
    ctx = _ctx(tmp_path, origin)

    await checkout_pull_request(ctx, PR, "main")
    assert ctx.source_changed is False

    (ctx.repo_path / "pr.txt").write_text("the agent's fix\n")
    assert ctx.source_changed is True


async def test_an_existing_checkout_is_moved_to_the_pr(tmp_path):
    # Production reuses a checkout left by earlier runs rather than cloning anew.
    origin = _origin(tmp_path)
    ensure_source_repo(tmp_path / "checkout", origin.path.as_uri())
    ctx = _ctx(tmp_path, origin)

    merge_base = await checkout_pull_request(ctx, PR, "main")

    assert _git(ctx.repo_path, "rev-parse", "HEAD") == origin.head
    assert merge_base == origin.fork_point


async def test_an_existing_checkout_discards_untracked_files(tmp_path):
    origin = _origin(tmp_path)
    checkout = tmp_path / "checkout"
    ensure_source_repo(checkout, origin.path.as_uri())
    (checkout / "leftover.txt").write_text("from an earlier run")
    (checkout / "leftover_dir").mkdir()
    (checkout / "leftover_dir" / "file.txt").write_text("from an earlier run")
    ctx = _ctx(tmp_path, origin)

    await checkout_pull_request(ctx, PR, "main")

    assert _git(ctx.repo_path, "status", "--porcelain") == ""
    assert ctx.source_changed is False
    assert not (checkout / "leftover.txt").exists()
    assert not (checkout / "leftover_dir").exists()


async def test_a_pr_pushed_to_since_the_request_is_refused(tmp_path):
    origin = _origin(tmp_path)
    ctx = _ctx(tmp_path, origin)

    with pytest.raises(RuntimeError, match="pushed to after this run"):
        await checkout_pull_request(ctx, PR, "main", head_sha="0" * 40)


async def test_a_missing_pr_is_reported(tmp_path):
    origin = _origin(tmp_path)
    ctx = _ctx(tmp_path, origin)

    with pytest.raises(RuntimeError, match="pull request #99"):
        await checkout_pull_request(ctx, 99, "main")


async def test_a_missing_base_is_reported(tmp_path):
    origin = _origin(tmp_path)
    ctx = _ctx(tmp_path, origin)

    with pytest.raises(RuntimeError, match="'no-such-branch', the base"):
        await checkout_pull_request(ctx, PR, "no-such-branch")


async def test_a_base_with_no_shared_history_is_refused(tmp_path, monkeypatch):
    # The search for a common commit is bounded, rather than ending in a fetch
    # of the repository's entire history.
    monkeypatch.setattr(pull_request, "_MAX_DEPTH", 32)
    origin = _origin(tmp_path)
    _git(origin.path, "switch", "-q", "--orphan", "unrelated")
    _commit(origin.path, "Unrelated", "other.txt", "other\n")
    ctx = _ctx(tmp_path, origin)

    with pytest.raises(RuntimeError, match="shares no history"):
        await checkout_pull_request(ctx, PR, "unrelated")
