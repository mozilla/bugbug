"""Prepare a source checkout for agents that operate on a code repository."""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("hackbot_runtime.source")

_GITHUB_REPO = re.compile(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?")


def _download(bucket: str, name: str):
    """Yield the bytes of ``gs://bucket/name`` as the agent's own identity."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
    )
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}"
        f"/o/{quote(name, safe='')}?alt=media"
    )
    with AuthorizedSession(credentials).get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        yield from resp.iter_content(chunk_size=1 << 20)


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
    log.info("restoring %s from gs://%s/%s", source_repo, bucket, name)
    try:
        source_repo.mkdir(parents=True, exist_ok=True)
        tar = subprocess.Popen(
            ["tar", "-x", "-C", str(source_repo)], stdin=subprocess.PIPE
        )
        try:
            for chunk in _download(bucket, name):
                tar.stdin.write(chunk)
        finally:
            tar.stdin.close()
            returncode = tar.wait()
        if returncode:
            raise RuntimeError(f"tar exited with {returncode}")
        for args in (
            ["remote", "set-url", "origin", repo_url],
            ["fetch", "origin", fetch_target],
            ["checkout", "-q", "-f", "FETCH_HEAD"],
        ):
            subprocess.run(
                ["git", "-C", str(source_repo), *args],
                check=True,
                stdout=sys.stderr,
                stderr=sys.stderr,
            )
    except Exception:
        log.warning("snapshot restore failed; cloning instead", exc_info=True)
        shutil.rmtree(source_repo, ignore_errors=True)
        return False
    log.info("snapshot checkout complete")
    return True


def ensure_source_repo(
    source_repo: Path, repo_url: str, ref: str | None = None, depth: int | None = None
) -> None:
    """Ensure a checkout of ``repo_url`` exists at ``source_repo``.

    A fresh checkout starts from the full-history snapshot when one is set up
    (``_restore_snapshot``) and is otherwise a shallow clone.

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
    if _restore_snapshot(source_repo, repo_url, fetch_target):
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
        ["git", "clone", depth_flag, repo_url, str(source_repo)],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    log.info("shallow clone complete")


def checkout_commit(source_repo: Path, ref: str) -> None:
    """Detach the checkout at ``ref``, which the shallow clone must already hold."""
    subprocess.run(
        ["git", "-C", str(source_repo), "checkout", "-q", "--detach", ref],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
