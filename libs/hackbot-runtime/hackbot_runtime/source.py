"""Prepare a source checkout for agents that operate on a code repository."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("hackbot_runtime.source")

# Who a commit made in a prepared checkout is committed by. Agent images
# configure no git identity, and `git commit` refuses to run without one.
COMMITTER_NAME = "Hackbot"
COMMITTER_EMAIL = "hackbot@mozilla.tld"


def ensure_source_repo(
    source_repo: Path, repo_url: str, ref: str | None = None, depth: int | None = None
) -> None:
    """Ensure a shallow checkout of ``repo_url`` exists at ``source_repo``.

    Idempotent: clones if absent, otherwise shallow-fetches and hard-resets to
    the requested ``ref`` (``origin/HEAD`` when ``ref`` is None). Recovers from a
    partial checkout left by an earlier failed run (e.g. the clone succeeded but
    the checkout ran out of disk).

    When ``ref`` is set (a commit/branch/tag), the repo is pinned there — useful
    for agents that must operate on a specific historical commit (e.g. a build
    failure commit) rather than the tip of the default branch.
    """
    checkout_source_repo(source_repo, repo_url, ref, depth)
    configure_git_identity(source_repo)


def configure_git_identity(source_repo: Path) -> None:
    """Give the checkout an identity, so an agent's own `git commit` works.

    Local to the checkout, which is ephemeral, and the committer only: a
    cherry-pick or an `--author` override keeps the author it replays.
    """
    for key, value in (("user.name", COMMITTER_NAME), ("user.email", COMMITTER_EMAIL)):
        subprocess.run(
            ["git", "-C", str(source_repo), "config", key, value],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )


def checkout_source_repo(
    source_repo: Path, repo_url: str, ref: str | None = None, depth: int | None = None
) -> None:
    """Clone or update the checkout itself; see :func:`ensure_source_repo`."""
    # Both the recovery path and the fresh clone converge on a shallow fetch of
    # this ref so a pinned commit is fetchable even when it is not on HEAD.
    fetch_target = ref if ref else "HEAD"
    # A pinned commit needs its parent too so the commit's own diff can be
    # computed (e.g. `git show <commit>`); depth=1 would fetch only the commit
    # itself with no parent to diff against. An explicit ``depth`` (e.g. to cover
    # every commit in a push so each is showable) overrides the default.
    if depth is not None:
        depth_flag = f"--depth={depth}"
    else:
        depth_flag = "--depth=2" if ref else "--depth=1"
    git_dir = source_repo / ".git"
    if git_dir.exists():
        # An earlier run killed mid-fetch (e.g. the container was stopped)
        # leaves stale lock files behind. Since each run drives git
        # sequentially, any lock present at startup is stale and safe to
        # remove.
        for lock in (git_dir / "shallow.lock", git_dir / "index.lock"):
            if lock.exists():
                log.warning("removing stale git lock %s", lock)
                lock.unlink()
        status = subprocess.run(
            ["git", "-C", str(source_repo), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        # A healthy fresh shallow clone has an empty status; a broken
        # checkout shows thousands of missing-file "D" entries.
        if status.stdout.strip():
            log.warning(
                "source at %s is incomplete; restoring working tree", source_repo
            )
            subprocess.run(
                ["git", "-C", str(source_repo), "restore", "--source=HEAD", ":/"],
                check=True,
                stdout=sys.stderr,
                stderr=sys.stderr,
            )
        log.info("updating source at %s (shallow fetch %s)", source_repo, fetch_target)
        subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "fetch",
                depth_flag,
                "origin",
                fetch_target,
            ],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        subprocess.run(
            ["git", "-C", str(source_repo), "reset", "--hard", "FETCH_HEAD"],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        return
    source_repo.mkdir(parents=True, exist_ok=True)
    if ref:
        # A bare clone can't fetch an arbitrary commit directly, so init an empty
        # repo and shallow-fetch just the requested ref.
        log.info("cloning %s (shallow) to %s at ref %s", repo_url, source_repo, ref)
        subprocess.run(
            ["git", "init", "-q", str(source_repo)],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        subprocess.run(
            ["git", "-C", str(source_repo), "remote", "add", "origin", repo_url],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        subprocess.run(
            ["git", "-C", str(source_repo), "fetch", depth_flag, "origin", ref],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        subprocess.run(
            ["git", "-C", str(source_repo), "checkout", "-q", "FETCH_HEAD"],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        log.info("shallow clone complete")
        return
    log.info("cloning %s (shallow) to %s", repo_url, source_repo)
    subprocess.run(
        ["git", "clone", "--depth=1", repo_url, str(source_repo)],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    log.info("shallow clone complete")
