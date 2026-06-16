"""Collect an agent's source-tree changes into a git-am patch + metadata.

After an agent runs, its work may be committed locally (each commit with its own
message/author) or left uncommitted/untracked. This module captures all of it,
relative to the commit the checkout started from, as a single mbox patch that
``git am`` applies in one command — preserving the local commit history — plus a
JSON summary of the commits and files touched.

Any uncommitted remainder is wrapped into one synthetic commit first, so nothing
is lost. The checkout is ephemeral (one run per container), so mutating its index
and creating that commit is safe.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger("hackbot_runtime.changes")

# Author stamped on the synthetic commit that wraps any uncommitted remainder.
_WIP_NAME = "Hackbot Agent"
_WIP_EMAIL = "hackbot@mozilla.tld"
_WIP_MESSAGE = "Uncommitted agent changes"

# Record separator for parsing ``git log`` output (NUL avoids clashing with
# anything in commit messages).
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


class ChangeSet(NamedTuple):
    """The collected agent changes: an mbox patch plus its metadata."""

    patch: bytes
    metadata: dict


def _git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo`` and return its stdout (text)."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _git_bytes(repo: Path, *args: str) -> bytes:
    """Run a git command in ``repo`` and return its stdout (bytes).

    Used for ``format-patch``, whose output may contain binary diffs.
    """
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def base_commit(repo: Path) -> str:
    """Return the current HEAD sha — the base the agent starts editing from."""
    return _git(repo, "rev-parse", "HEAD").strip()


def _has_uncommitted(repo: Path) -> bool:
    return bool(_git(repo, "status", "--porcelain").strip())


def _wrap_uncommitted(repo: Path) -> bool:
    """Commit any staged/unstaged/untracked changes into one synthetic commit.

    Returns ``True`` if such a commit was created, ``False`` if the tree was
    already clean.
    """
    if not _has_uncommitted(repo):
        return False
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        f"user.name={_WIP_NAME}",
        "-c",
        f"user.email={_WIP_EMAIL}",
        "commit",
        "--no-verify",
        "-m",
        _WIP_MESSAGE,
    )
    return True


def _commit_metadata(repo: Path, base: str) -> list[dict]:
    """Structured info for each commit in ``base..HEAD`` (oldest first)."""
    fmt = _FIELD_SEP.join(["%H", "%an", "%ae", "%aI", "%s", "%b"]) + _RECORD_SEP
    out = _git(repo, "log", "--reverse", f"--format={fmt}", f"{base}..HEAD")
    commits = []
    for record in out.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, author_name, author_email, authored_date, subject, body = record.split(
            _FIELD_SEP
        )
        commits.append(
            {
                "sha": sha,
                "author_name": author_name,
                "author_email": author_email,
                "authored_date": authored_date,
                "subject": subject,
                "body": body,
            }
        )
    return commits


def collect(repo: Path, base: str) -> ChangeSet | None:
    """Collect changes in ``repo`` since ``base`` as a patch plus metadata.

    Returns ``None`` when the agent made no changes at all (nothing committed and
    a clean working tree). Otherwise returns a :class:`ChangeSet` whose ``patch``
    is an mbox (``git format-patch`` output) applied with ``git am`` and whose
    ``metadata`` describes the base, the commits, and the files touched.
    """
    wrapped = _wrap_uncommitted(repo)
    patch = _git_bytes(repo, "format-patch", "--stdout", "--binary", f"{base}..HEAD")
    if not patch.strip():
        return None
    metadata = {
        "base_commit": base,
        "wrapped_uncommitted": wrapped,
        "commits": _commit_metadata(repo, base),
    }
    return ChangeSet(patch=patch, metadata=metadata)
