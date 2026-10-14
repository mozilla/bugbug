"""Check an agent's source tree out at a GitHub pull request's head."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hackbot_runtime.context import HackbotContext

log = logging.getLogger(__name__)

# How much history to fetch, from both the head and the base, while looking for
# the commit the pull request branched from. The source is a shallow clone, so
# that commit is usually missing at first. The depth doubles each round, so a PR
# close to its base costs a few commits, and one that is far behind (or shares
# no history with it) stops at a bounded fetch instead of pulling the
# repository's whole history.
_FIRST_DEPTH = 16
_MAX_DEPTH = 4096


async def checkout_pull_request(
    ctx: HackbotContext,
    number: int,
    base: str,
    head_sha: str | None = None,
) -> str:
    """Prepare the source so it matches pull request ``number`` before the agent runs.

    The checkout is left at the PR's head, committed and clean: the PR's commits
    are the agent's starting point, not its work, so what the run publishes is
    only what the agent changed on top of them (which is also what a later push
    to the PR's branch would add).

    ``base`` is the branch or commit the PR targets (its ``base.ref`` or
    ``base.sha``). It is fetched with enough history on both sides for git to
    find the commit the PR branched from. Use the returned merge base with
    ``git diff <merge-base> HEAD`` to see the same change as GitHub's "Files
    changed" tab, or ``git log <merge-base>..HEAD`` to list the PR's commits.

    ``head_sha``, when given, is the full sha the caller saw the PR at (for
    example when the run was requested). If the PR has been pushed to since, this
    raises rather than working on code nobody asked about.

    The PR must belong to the agent's source repository (``[source].repo_url``),
    and that repository must be readable without credentials.

    Returns the merge base: the commit the PR's changes are measured from.

    Raises :class:`RuntimeError` if the PR or its base cannot be fetched, the
    head is not ``head_sha``, or no common history with the base turns up, so
    the run fails visibly rather than working on the wrong tree.
    """
    head_ref = f"refs/pull/{number}/head"
    try:
        # Must run before anything else touches the source (prepare_repo raises
        # otherwise). It also records the head as the agent's starting point.
        repo = await ctx.prepare_repo(ref=head_ref)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Could not fetch pull request #{number} ({head_ref}). Check that it "
            "exists in the agent's source repository."
        ) from exc

    head = _git(repo, "rev-parse", "HEAD")
    if head_sha is not None and head != head_sha:
        raise RuntimeError(
            f"Pull request #{number} is at {head}, not {head_sha}: it was pushed "
            "to after this run was requested. Request a new run to work on the "
            "new head."
        )

    # prepare_repo resets tracked files, but reused checkouts can retain untracked files.
    _git(repo, "clean", "-fd")
    if _git(repo, "status", "--porcelain"):
        raise RuntimeError(f"Pull request #{number} checkout is not clean")

    # Fetching the base with --depth on a full clone would truncate its history,
    # so only stay shallow if the checkout already is.
    shallow = _git(repo, "rev-parse", "--is-shallow-repository") == "true"
    try:
        _fetch(repo, base, depth=1 if shallow else None)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Could not fetch {base!r}, the base of pull request #{number}: "
            f"{exc.stderr.strip()}"
        ) from exc
    base_sha = _git(repo, "rev-parse", "FETCH_HEAD")
    log.info("Checked out pull request #%s at %s (base %s)", number, head, base_sha)

    merge_base = _merge_base(repo, base_sha, head)
    depth = _FIRST_DEPTH
    while merge_base is None and shallow and depth <= _MAX_DEPTH:
        # Both sides, by sha: the shared commit is behind the head by the PR's
        # own commits, and behind the base by whatever landed since the PR
        # branched. Fetching shas rather than refs keeps either side from moving
        # under us mid-search.
        _fetch(repo, head, base_sha, depth=depth)
        merge_base = _merge_base(repo, base_sha, head)
        depth *= 2

    if merge_base is None:
        raise RuntimeError(
            f"Pull request #{number} shares no history with its base {base!r} "
            f"within {_MAX_DEPTH} commits. If it is that far behind, it has to "
            "be rebased before hackbot can work on it."
        )
    return merge_base


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _fetch(repo: Path, *refs: str, depth: int | None) -> None:
    depth_flag = [f"--depth={depth}"] if depth is not None else []
    _git(repo, "fetch", "-q", *depth_flag, "origin", *refs)


def _merge_base(repo: Path, base: str, head: str) -> str | None:
    """The commit ``head`` branched from ``base`` at, if the checkout holds it."""
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", base, head],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None
