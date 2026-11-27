"""Tests for fetching the sources' commits before the agent's session starts."""

from pathlib import Path

import pytest
from hackbot_agents.uplift_resolve import agent
from hackbot_agents.uplift_resolve.models import (
    FetchedDiff,
    GitSource,
    PhabricatorSource,
)
from hackbot_runtime import AgentError


def build_upstream(git_in, path: Path) -> tuple[str, str]:
    """A remote with a trunk commit to uplift, and a stable branch without it.

    Returns the commit to uplift and its parent.
    """
    path.mkdir()
    git_in(path, "init", "-q", "-b", "main")
    git_in(path, "config", "user.email", "trunk@example.com")
    git_in(path, "config", "user.name", "Trunk Dev")
    (path / "f.txt").write_text("line1\n")
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "base")
    git_in(path, "branch", "stable")
    (path / "f.txt").write_text("line1\nline2\n")
    git_in(path, "commit", "-qam", "parent of the patch")
    parent = git_in(path, "rev-parse", "HEAD")
    (path / "f.txt").write_text("line1\nline2 patched\n")
    git_in(path, "commit", "-qam", "patch to uplift")
    return git_in(path, "rev-parse", "HEAD"), parent


def shallow_stable_checkout(git_in, upstream: Path, path: Path) -> Path:
    """A shallow checkout of the stable branch, as the runtime prepares one."""
    git_in(
        path.parent,
        "clone",
        "-q",
        "--depth=1",
        "--branch",
        "stable",
        upstream.as_uri(),
        path.name,
    )
    return path


def has_commit(git_in, repo: Path, commit: str) -> bool:
    return git_in(repo, "cat-file", "-t", commit, check=False) == "commit"


async def test_git_sources_are_fetched_with_their_parent(tmp_path, git_in):
    to_uplift, parent = build_upstream(git_in, tmp_path / "upstream")
    checkout = shallow_stable_checkout(
        git_in, tmp_path / "upstream", tmp_path / "checkout"
    )
    assert not has_commit(git_in, checkout, to_uplift), (
        "The shallow stable checkout should start without the commit to uplift."
    )

    await agent.fetch_source_commits(checkout, [GitSource(commit=to_uplift)], [None])

    assert has_commit(git_in, checkout, to_uplift), (
        "The commit to uplift should be fetched before the session starts."
    )
    assert has_commit(git_in, checkout, parent), (
        "Its parent should be fetched too, so cherry-pick can three-way merge."
    )


async def test_a_git_source_that_cannot_be_fetched_fails_the_run(tmp_path, git_in):
    build_upstream(git_in, tmp_path / "upstream")
    checkout = shallow_stable_checkout(
        git_in, tmp_path / "upstream", tmp_path / "checkout"
    )

    with pytest.raises(AgentError, match="could not fetch"):
        await agent.fetch_source_commits(checkout, [GitSource(commit="f" * 40)], [None])


async def test_a_phabricator_base_that_cannot_be_fetched_is_skipped(tmp_path, git_in):
    to_uplift, _ = build_upstream(git_in, tmp_path / "upstream")
    checkout = shallow_stable_checkout(
        git_in, tmp_path / "upstream", tmp_path / "checkout"
    )
    diff_path = tmp_path / "D1-2.diff"
    fetched = [
        FetchedDiff(path=diff_path, author=None, base_commit=to_uplift, diff_id=2),
        FetchedDiff(path=diff_path, author=None, base_commit="f" * 40, diff_id=3),
    ]
    sources = [
        PhabricatorSource(revision_id=1, diff_id=2),
        PhabricatorSource(revision_id=1, diff_id=3),
    ]

    await agent.fetch_source_commits(checkout, sources, fetched)

    assert has_commit(git_in, checkout, to_uplift), (
        "A Phabricator source's base commit should be fetched for `git apply --3way`."
    )


def build_stack_upstream(git_in, path: Path) -> list[str]:
    """A remote with a three-commit stack on trunk. Returns the stack's commits."""
    path.mkdir()
    git_in(path, "init", "-q", "-b", "main")
    git_in(path, "config", "user.email", "trunk@example.com")
    git_in(path, "config", "user.name", "Trunk Dev")
    (path / "f.txt").write_text("base\n")
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "base")
    git_in(path, "branch", "stable")
    stack = []
    for name in ("parent", "first", "second", "third"):
        (path / "f.txt").write_text(f"{name}\n")
        git_in(path, "commit", "-qam", name)
        stack.append(git_in(path, "rev-parse", "HEAD"))
    return stack[1:]


async def test_a_stack_keeps_every_source_attached_to_its_parent(tmp_path, git_in):
    stack = build_stack_upstream(git_in, tmp_path / "upstream")
    checkout = shallow_stable_checkout(
        git_in, tmp_path / "upstream", tmp_path / "checkout"
    )

    await agent.fetch_source_commits(
        checkout, [GitSource(commit=commit) for commit in stack], [None] * len(stack)
    )

    shallow_roots = agent.read_shallow_roots(checkout)
    assert not shallow_roots & set(stack), (
        "No source in a stack should be left without its parent, or cherry-picking "
        "it would add the whole tree."
    )
    for commit in stack:
        assert has_commit(git_in, checkout, f"{commit}^"), (
            "Each source's parent should be local, for a three-way cherry-pick."
        )
