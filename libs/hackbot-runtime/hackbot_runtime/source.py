"""Prepare a source checkout for agents that operate on a code repository."""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

from google.cloud import storage

log = logging.getLogger("hackbot_runtime.source")

_GITHUB_REPO = re.compile(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?")


def _git(source_repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(source_repo), *args],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )


def _download(bucket: str, name: str, out: BinaryIO) -> None:
    """Write ``gs://bucket/name`` to ``out``, checking its checksum."""
    # crc32c: large uploads are composite objects, which have no md5.
    storage.Client().bucket(bucket).blob(name).download_to_file(out, checksum="crc32c")


def _check_out_snapshot(
    source_repo: Path, bucket: str, name: str, repo_url: str, fetch_target: str
) -> None:
    log.info("restoring %s from gs://%s/%s", source_repo, bucket, name)
    source_repo.mkdir(parents=True, exist_ok=True)
    # Stream straight into tar, so the tar never takes disk space of its own.
    tar = subprocess.Popen(
        ["tar", "-x", "-f", "-", "-C", str(source_repo)], stdin=subprocess.PIPE
    )
    try:
        _download(bucket, name, tar.stdin)
    finally:
        tar.stdin.close()
        returncode = tar.wait()
    if returncode:
        raise subprocess.CalledProcessError(returncode, tar.args)
    log.info("snapshot unpacked; fetching %s", fetch_target)
    # Name the unpacked repository, so a tar without .git fails here instead of
    # git finding an enclosing repository. Relative to -C, so relative paths work.
    git_dir = ("--git-dir=.git", "--work-tree=.")
    _git(source_repo, *git_dir, "remote", "set-url", "origin", repo_url)
    _git(source_repo, *git_dir, "fetch", "origin", fetch_target)
    _git(source_repo, *git_dir, "checkout", "-q", "-f", "FETCH_HEAD")
    log.info("snapshot checkout complete")


def _restore_snapshot(source_repo: Path, repo_url: str, fetch_target: str) -> bool:
    """Check out ``fetch_target`` from the full-history snapshot, if one is set up.

    The snapshot is a daily ``.git`` tar (``SOURCE_SNAPSHOT_BUCKET``); fetching
    then adds only newer commits. Returns False, leaving nothing behind, when
    there is none or any step fails, so the caller can clone instead.
    """
    bucket = os.environ.get("SOURCE_SNAPSHOT_BUCKET")
    match = _GITHUB_REPO.fullmatch(repo_url)
    # Only into an empty directory, so a failure can remove all of it.
    if not bucket or not match or (source_repo.exists() and any(source_repo.iterdir())):
        return False
    name = f"github/{match[1]}.tar"
    try:
        _check_out_snapshot(source_repo, bucket, name, repo_url, fetch_target)
    except Exception:
        # Any failure (download, checksum, tar, git) is recoverable by cloning.
        log.warning("snapshot restore failed; cloning instead", exc_info=True)
        shutil.rmtree(source_repo, ignore_errors=True)
        return False
    return True


def _update_checkout(source_repo: Path, fetch_target: str, depth: int) -> None:
    """Refresh an existing checkout to ``fetch_target``, repairing a broken one."""
    git_dir = source_repo / ".git"
    # An earlier run killed mid-fetch (e.g. the container was stopped) leaves
    # stale lock files behind. Since each run drives git sequentially, any lock
    # present at startup is stale and safe to remove.
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
    # A healthy fresh shallow clone has an empty status; a broken checkout shows
    # thousands of missing-file "D" entries.
    if status.stdout.strip():
        log.warning("source at %s is incomplete; restoring working tree", source_repo)
        _git(source_repo, "restore", "--source=HEAD", ":/")
    log.info("updating source at %s (shallow fetch %s)", source_repo, fetch_target)
    _git(source_repo, "fetch", f"--depth={depth}", "origin", fetch_target)
    _git(source_repo, "reset", "--hard", "FETCH_HEAD")


def _shallow_clone(
    source_repo: Path, repo_url: str, ref: str | None, depth: int
) -> None:
    source_repo.mkdir(parents=True, exist_ok=True)
    if ref:
        # A bare clone can't fetch an arbitrary commit directly, so init an empty
        # repo and shallow-fetch just the requested ref.
        log.info("cloning %s (shallow) to %s at ref %s", repo_url, source_repo, ref)
        _git(source_repo, "init", "-q")
        _git(source_repo, "remote", "add", "origin", repo_url)
        _git(source_repo, "fetch", f"--depth={depth}", "origin", ref)
        _git(source_repo, "checkout", "-q", "FETCH_HEAD")
    else:
        log.info("cloning %s (shallow) to %s", repo_url, source_repo)
        _git(source_repo, "clone", f"--depth={depth}", repo_url, ".")
    log.info("shallow clone complete")


def ensure_source_repo(
    source_repo: Path, repo_url: str, ref: str | None = None, depth: int | None = None
) -> None:
    """Ensure a checkout of ``repo_url`` exists at ``source_repo``.

    Idempotent: an existing checkout is shallow-fetched and hard-reset to the
    requested ``ref`` (``origin/HEAD`` when ``ref`` is None), recovering from a
    partial checkout left by an earlier failed run. A fresh checkout starts from
    the full-history snapshot when one is set up, and is otherwise a shallow
    clone.

    When ``ref`` is set (a commit/branch/tag), the repo is pinned there — useful
    for agents that must operate on a specific historical commit (e.g. a build
    failure commit) rather than the tip of the default branch.
    """
    # Every path fetches this ref, so a pinned commit is fetchable even when it
    # is not on HEAD.
    fetch_target = ref or "HEAD"
    # A pinned commit needs its parent too so the commit's own diff can be
    # computed (e.g. `git show <commit>`). An explicit ``depth`` (e.g. to cover
    # every commit in a push so each is showable) overrides the default.
    if depth is None:
        depth = 2 if ref else 1
    if (source_repo / ".git").exists():
        _update_checkout(source_repo, fetch_target, depth)
    elif not _restore_snapshot(source_repo, repo_url, fetch_target):
        _shallow_clone(source_repo, repo_url, ref, depth)


def checkout_commit(source_repo: Path, ref: str) -> None:
    """Detach the checkout at ``ref``, which the shallow clone must already hold."""
    _git(source_repo, "checkout", "-q", "--detach", ref)
