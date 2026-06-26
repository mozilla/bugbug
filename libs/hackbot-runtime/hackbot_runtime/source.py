"""Prepare a source checkout for agents that operate on a code repository."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("hackbot_runtime.source")


def ensure_source_repo(
    source_repo: Path, repo_url: str, ref: str | None = None
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
    # Both the recovery path and the fresh clone converge on a shallow fetch of
    # this ref so a pinned commit is fetchable even when it is not on HEAD.
    fetch_target = ref if ref else "HEAD"
    # A pinned commit needs its parent too so the commit's own diff can be
    # computed (e.g. `git show <commit>`); depth=1 would fetch only the commit
    # itself with no parent to diff against.
    depth = "--depth=2" if ref else "--depth=1"
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
                depth,
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
            ["git", "-C", str(source_repo), "fetch", depth, "origin", ref],
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
