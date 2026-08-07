"""Check an agent's source tree out at a Phabricator revision before it runs.

For a follow-up run (e.g. an ``@hackbot`` mention) we want the agent to operate
on the revision's actual code (its base commit + its latest diff), not a clean
base checkout.

The agent holds no credentials, so it does not talk to Conduit itself: it asks a
broker sidecar (which holds the Phabricator key) for the revision's base commit +
the patches to replay onto it over a keyless loopback URL, then checks out that
base and applies them locally (``git apply`` needs no key). The broker endpoint
contract is ``GET {broker_url}/phabricator/revision/{id}/patch`` ->
``{base_commit, patches: [{revision_id, diff_id, raw_diff}]}``, bottom-first.

There is more than one patch when the revision is stacked on parent revisions
that have not landed: the commit it was built on then exists only in the
author's repository, so no remote can fetch it and the tree is rebuilt by
replaying the parents' diffs onto the closest base that is fetchable.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from hackbot_runtime import changes

if TYPE_CHECKING:
    from hackbot_runtime.context import HackbotContext

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0)


async def checkout_revision(
    ctx: HackbotContext,
    revision_id: int,
    broker_url: str,
) -> None:
    """Prepare the source at the revision's base commit and apply its diff.

    Fetches the base commit + patches from the broker (``broker_url``, a keyless
    loopback URL). Raises :class:`RuntimeError` if the broker can't provide the
    patches or one does not apply cleanly, so the run fails visibly rather than
    editing the wrong tree.

    The revision's own diff is left uncommitted, so the run's recorded change
    base stays at the revision's base and the final submission is the complete,
    updated revision (base -> revision + the agent's follow-up edits). The
    diffs of any unlanded parent revisions are committed first and the change
    base is moved on top of them: they set the scene for the run, but they
    belong to their own revisions and must not reappear in this one's diff.
    """
    url = f"{broker_url.rstrip('/')}/phabricator/revision/{revision_id}/patch"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise RuntimeError(
            f"Broker could not provide patch for D{revision_id} "
            f"(HTTP {response.status_code}): {response.text.strip()}"
        )
    payload = response.json()
    base = payload["base_commit"]
    patches = payload["patches"]
    if not patches:
        raise RuntimeError(f"Broker returned no patches for D{revision_id}")

    # Prepare the checkout explicitly at the base commit, then apply the patches
    # onto the working tree so the tree matches the revision. Must run before
    # anything else touches the source (prepare_repo raises otherwise).
    repo = await ctx.prepare_repo(ref=base)

    log.info("Checking out D%s (base %s) before running the agent", revision_id, base)
    *ancestors, revision_patch = patches
    for patch in ancestors:
        log.info(
            "Restoring unlanded parent D%s (diff %s) of D%s",
            patch["revision_id"],
            patch["diff_id"],
            revision_id,
        )
        _apply(repo, patch, base)
        # Committed with the runtime's own identity: this stands in for a commit
        # nobody but the author has, and only its tree matters.
        changes.commit_all(
            repo,
            f"D{patch['revision_id']} diff {patch['diff_id']} "
            f"(unlanded parent of D{revision_id})",
        )
    if ancestors:
        # The parents are now history, not this run's work.
        ctx.reset_source_base()

    _apply(repo, revision_patch, base)


def _apply(repo: Path, patch: dict, base: str) -> None:
    """Apply one revision's raw diff onto the working tree, or raise."""
    result = subprocess.run(
        ["git", "-C", str(repo), "apply"],
        input=patch["raw_diff"].encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not apply diff for D{patch['revision_id']} onto {base}: "
            f"{result.stderr.decode().strip()}"
        )
