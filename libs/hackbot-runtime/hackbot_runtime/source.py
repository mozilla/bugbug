"""Prepare a source checkout for agents that operate on a code repository."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("hackbot_runtime.source")


def ensure_source_repo(source_repo: Path, repo_url: str) -> None:
    """Ensure a shallow checkout of ``repo_url`` exists at ``source_repo``.

    Idempotent: clones if absent, otherwise shallow-fetches and hard-resets to
    the remote HEAD. Recovers from a partial checkout left by an earlier failed
    run (e.g. the clone succeeded but the checkout ran out of disk).
    """
    if (source_repo / ".git").exists():
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
        log.info("updating source at %s (shallow fetch)", source_repo)
        subprocess.run(
            ["git", "-C", str(source_repo), "fetch", "--depth=1", "origin", "HEAD"],
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
    log.info("cloning %s (shallow) to %s", repo_url, source_repo)
    subprocess.run(
        ["git", "clone", "--depth=1", repo_url, str(source_repo)],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    log.info("shallow clone complete")
