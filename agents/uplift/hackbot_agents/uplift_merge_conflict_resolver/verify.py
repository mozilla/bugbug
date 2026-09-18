"""Mechanical checks on a finished uplift.

The agent grades its own work and a caller gates landing on `resolved`, so it
is checked against the repository rather than trusted. Confidence stays the
agent's own judgment; none of these are.
"""

import subprocess
from pathlib import Path

from .config import Report


def git_output(repo: Path, *args: str) -> str:
    """Run a read-only git command in ``repo`` and return its stripped stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def head_commit(repo: Path) -> str:
    """The commit ``repo`` is currently on."""
    return git_output(repo, "rev-parse", "HEAD")


def verify_uplift(repo: Path, base_commit: str, report: Report) -> list[str]:
    """What the checkout and the report say is wrong, whatever the agent claimed.

    One line per problem, empty when the run is fit to hand on.
    """
    problems = []

    if report.resolved and report.unresolved:
        problems.append(
            f"report claims resolved while listing {len(report.unresolved)} "
            f"unresolved item(s)"
        )

    unmerged = git_output(repo, "diff", "--name-only", "--diff-filter=U")
    if unmerged:
        problems.append(f"unresolved conflicts still in the index: {unmerged.split()}")

    # Mid-operation the tree looks plausible, but the patch would be a fragment.
    git_dir = Path(git_output(repo, "rev-parse", "--absolute-git-dir"))
    for marker, operation in (
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
    ):
        if (git_dir / marker).exists():
            problems.append(f"{operation} left unfinished in the checkout")
            break

    # Uncommitted work is swept into a container-authored commit, losing the
    # original author.
    dirty = git_output(repo, "status", "--porcelain")
    if dirty:
        problems.append(f"{len(dirty.splitlines())} path(s) left uncommitted")

    # A claimed resolution has to leave a patch behind it, and HEAD simply
    # differing from the base does not mean it does: HEAD can move backwards,
    # and an empty commit moves it while changing nothing. This is what the
    # runtime will collect, so it is checked the way the runtime collects it.
    # A run reporting failure is exempt -- having nothing to collect is honest.
    if report.resolved:
        if git_output(repo, "merge-base", base_commit, "HEAD") != base_commit:
            problems.append(
                f"HEAD no longer descends from the commit the run started at "
                f"({base_commit[:12]})"
            )
        elif not git_output(repo, "rev-list", f"{base_commit}..HEAD"):
            problems.append("report claims resolved but no commits were produced")
        elif not git_output(repo, "diff", "--name-only", base_commit, "HEAD"):
            problems.append("report claims resolved but the commits change nothing")

    return problems
