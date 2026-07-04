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


def _synthetic_commit(repo: Path, base: str) -> str:
    """Create a detached commit object squashing ``base..HEAD``'s tree.

    Doesn't touch the working tree, index, or branch pointer — `commit-tree`
    just writes one new commit object with `base` as its sole parent, giving
    `build_phabricator_diff` a single commit to diff (moz-phab's diff-tree
    based diffing only supports one commit vs. its immediate parent, no
    range).
    """
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    # Pass an explicit identity (as _wrap_uncommitted does): the synthetic
    # commit's author is throwaway — only its tree diff is used — but
    # `commit-tree` errors under `user.useConfigOnly=true` and otherwise
    # invents a `user@hostname` author when the container has no git identity
    # configured. A fixed identity keeps it deterministic and unconditional.
    return _git(
        repo,
        "-c",
        f"user.name={_WIP_NAME}",
        "-c",
        f"user.email={_WIP_EMAIL}",
        "commit-tree",
        tree,
        "-p",
        base,
        "-m",
        "hackbot: squashed changes for Phabricator diff",
    ).strip()


def build_phabricator_diff(repo: Path, base: str, repo_url: str) -> dict | None:
    """Build the payload for Phabricator's ``differential.creatediff`` API.

    Uses ``moz-phab``'s own diff-building code (``mozphab.git``/``mozphab.diff``,
    imported as a library — requires the ``hackbot-runtime[phabricator]``
    extra) against ``repo``, which the agent already has fully checked out
    for its own work — no separate clone or checkout happens here. Returns
    ``None`` if building the diff fails for any reason (e.g. the checkout
    lacks an ``.arcconfig``, or nothing actually changed) — this is
    best-effort, gated by the caller on whether a `phabricator.submit_patch`
    action was even recorded, so a failure here shouldn't break an otherwise
    successful run.

    ``repositoryPHID`` is deliberately not included here — it's resolved by
    the apply-side handler instead, since it's specific to which Phabricator
    instance/environment (staging vs. prod) the diff actually gets submitted
    to, and that shouldn't be baked into an artifact built at agent-run time.
    """
    try:
        from mozphab.args import parse_args
        from mozphab.commits import Commit
        from mozphab.git import Git
    except ImportError:
        log.warning(
            "hackbot-runtime[phabricator] extra not installed; "
            "cannot build a Phabricator diff"
        )
        return None

    try:
        node = _synthetic_commit(repo, base)
        mozphab_repo = Git(str(repo))
        # `set_args` needs a fully-populated argparse.Namespace matching what
        # moz-phab's own CLI would build (several unrelated code paths read
        # attributes off it) — going through its real parser instead of
        # hand-listing the handful of attributes get_diff() happens to touch
        # today, which would silently bit-rot on a moz-phab upgrade.
        mozphab_repo.set_args(parse_args(["submit", "--yes"]))
        diff = mozphab_repo.get_diff(Commit(node=node))
    except Exception:
        log.warning("Could not build Phabricator diff for %s", repo, exc_info=True)
        return None

    changes_payload = [change.to_conduit(node) for change in diff.changes.values()]
    if not changes_payload:
        return None

    return {
        "changes": changes_payload,
        "sourceMachine": repo_url,
        "sourcePath": str(repo),
        "sourceControlBaseRevision": base,
        "sourceControlPath": "/",
        "sourceControlSystem": "git",
        "branch": "HEAD",
        "creationMethod": "hackbot",
        "lintStatus": "none",
        "unitStatus": "none",
    }


def collect(repo: Path, base: str, repo_url: str) -> ChangeSet | None:
    """Collect changes in ``repo`` since ``base`` as a patch plus metadata.

    Returns ``None`` when the agent made no changes at all (nothing committed and
    a clean working tree). Otherwise returns a :class:`ChangeSet` whose ``patch``
    is an mbox (``git format-patch`` output) applied with ``git am`` and whose
    ``metadata`` describes the base, the commits, and the files touched.

    ``repo_url`` is carried into the metadata (not derived from ``repo``, a local
    path) so a later, out-of-process apply step — e.g. the Phabricator submit
    handler, which needs to re-check-out this same base commit — knows where
    to clone from without re-deriving agent-specific config.
    """
    wrapped = _wrap_uncommitted(repo)
    patch = _git_bytes(repo, "format-patch", "--stdout", "--binary", f"{base}..HEAD")
    if not patch.strip():
        return None
    metadata = {
        "base_commit": base,
        "repo_url": repo_url,
        "wrapped_uncommitted": wrapped,
        "commits": _commit_metadata(repo, base),
    }
    return ChangeSet(patch=patch, metadata=metadata)
