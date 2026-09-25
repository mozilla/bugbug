"""Tests for the mechanical checks applied to a finished uplift.

Real repositories throughout: every check is a statement about git's own
state, which a stub could only assert we asked for.
"""

from __future__ import annotations

from pathlib import Path

from hackbot_agents.uplift_merge_conflict_resolver.models import Report
from hackbot_agents.uplift_merge_conflict_resolver.verify import verify_uplift

RESOLVED = Report(resolved=True, confidence="high", summary="done")


def commit(git_in, repo: Path, content: str, message: str = "uplifted") -> None:
    (repo / "f.txt").write_text(content)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", message)


def conflicted_cherry_pick(git_in, repo: Path) -> str:
    """Leave ``repo`` mid-cherry-pick with a conflict, and return the base.

    The state a real failed uplift arrives in.
    """
    base = git_in(repo, "rev-parse", "HEAD")
    git_in(repo, "checkout", "-q", "-b", "theirs")
    commit(git_in, repo, "line1\nline2 theirs\nline3\n", "theirs")
    git_in(repo, "checkout", "-q", base)
    git_in(repo, "checkout", "-q", "-b", "ours")
    commit(git_in, repo, "line1\nline2 ours\nline3\n", "ours")
    git_in(repo, "cherry-pick", "theirs", check=False)
    return base


def test_a_committed_resolution_passes(repo, git_in):
    base = git_in(repo, "rev-parse", "HEAD")
    commit(git_in, repo, "line1\nline2 uplifted\nline3\n")

    assert verify_uplift(repo, base, RESOLVED) == [], (
        "A clean tree with a new commit is what a good run leaves behind."
    )


def test_a_failed_run_with_nothing_to_show_passes(repo, git_in):
    base = git_in(repo, "rev-parse", "HEAD")

    assert verify_uplift(repo, base, Report(resolved=False)) == [], (
        "A run reporting failure that produced nothing is self-consistent; "
        "there is no patch to guard."
    )


def test_a_claimed_resolution_with_no_commits_fails(repo, git_in):
    base = git_in(repo, "rev-parse", "HEAD")

    problems = verify_uplift(repo, base, RESOLVED)

    assert any("no commits were produced" in problem for problem in problems), (
        "A resolution with an unmoved HEAD has no patch behind it."
    )


def test_a_rewound_head_fails(repo, git_in):
    """The runtime collects `base..HEAD`, which is empty once HEAD moves back."""
    commit(git_in, repo, "line1\nline2 second\nline3\n", "second")
    base = git_in(repo, "rev-parse", "HEAD")
    git_in(repo, "reset", "--hard", "-q", "HEAD~")

    problems = verify_uplift(repo, base, RESOLVED)

    assert any("no longer descends" in problem for problem in problems), (
        "HEAD differing from the base is not enough: it can differ by moving "
        "backwards, which collects to no patch at all."
    )


def test_an_empty_commit_fails(repo, git_in):
    base = git_in(repo, "rev-parse", "HEAD")
    git_in(repo, "commit", "-q", "--allow-empty", "-m", "nothing at all")

    problems = verify_uplift(repo, base, RESOLVED)

    assert any("change nothing" in problem for problem in problems), (
        "An empty commit moves HEAD without uplifting anything."
    )


def test_uncommitted_work_fails(repo, git_in):
    base = git_in(repo, "rev-parse", "HEAD")
    commit(git_in, repo, "line1\nline2 uplifted\nline3\n")
    (repo / "f.txt").write_text("line1\nline2 forgotten\nline3\n")

    problems = verify_uplift(repo, base, RESOLVED)

    assert any("uncommitted" in problem for problem in problems), (
        "The runtime sweeps uncommitted work into one synthetic commit, "
        "collapsing the stack the sources were applied as."
    )


def test_a_conflicted_index_fails(repo, git_in):
    base = conflicted_cherry_pick(git_in, repo)

    problems = verify_uplift(repo, base, RESOLVED)

    assert any("unresolved conflicts" in problem for problem in problems), (
        "A path still in a conflicted state must not pass as resolved."
    )
    assert any("cherry-pick left unfinished" in problem for problem in problems), (
        "The tree is mid-operation rather than at a finished commit."
    )


def test_an_unfinished_cherry_pick_fails_even_with_a_clean_index(repo, git_in):
    """Conflicts staged but never committed: the index is clean, the pick is not."""
    base = conflicted_cherry_pick(git_in, repo)
    (repo / "f.txt").write_text("line1\nline2 resolved\nline3\n")
    git_in(repo, "add", "-A")

    problems = verify_uplift(repo, base, RESOLVED)

    assert any("cherry-pick left unfinished" in problem for problem in problems), (
        "Staging a resolution is not committing it; the pick is still open."
    )


def test_a_report_claiming_resolved_while_listing_unresolved_fails(repo, git_in):
    base = git_in(repo, "rev-parse", "HEAD")
    commit(git_in, repo, "line1\nline2 uplifted\nline3\n")

    problems = verify_uplift(
        repo,
        base,
        Report(resolved=True, unresolved=["a.cpp: feature absent on the branch"]),
    )

    assert any("claims resolved while listing" in problem for problem in problems), (
        "`resolved` and a non-empty `unresolved` contradict each other."
    )
