"""Check an agent's source tree out at a Phabricator revision before it runs.

For a follow-up run (e.g. an ``@hackbot`` mention) we want the agent to operate
on the revision's actual code (its base commit + its latest diff), not a clean
base checkout.

The agent holds no credentials, so it does not talk to Conduit itself: it asks a
broker sidecar (which holds the Phabricator key) for the revision's base commit +
raw diff over a keyless loopback URL, then checks out that base and applies the
diff locally (``git apply`` needs no key). The broker endpoint contract is
``GET {broker_url}/phabricator/revision/{id}/patch`` -> ``{base_commit, raw_diff}``.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

import httpx

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

    Fetches the base commit + raw diff from the broker (``broker_url``, a keyless
    loopback URL). Raises :class:`RuntimeError` if the broker can't provide the
    patch or the diff does not apply cleanly — so the run fails visibly rather
    than editing the wrong tree.

    The diff is left uncommitted, so the run's recorded change base stays at the
    revision's base commit and the final submission is the complete, updated
    revision (base -> revision + the agent's follow-up edits).
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
    raw_diff = payload["raw_diff"]

    # Prepare the checkout explicitly at the revision's base commit, then apply
    # the diff onto the working tree so the tree matches the revision. Must run
    # before anything else touches the source (prepare_repo raises otherwise).
    repo = await ctx.prepare_repo(ref=base)

    log.info("Checking out D%s (base %s) before running the agent", revision_id, base)
    result = subprocess.run(
        ["git", "-C", str(repo), "apply"],
        input=raw_diff.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not apply diff for D{revision_id} onto {base}: "
            f"{result.stderr.decode().strip()}"
        )
