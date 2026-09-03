"""Check an agent's source tree out at a Phabricator revision, stack included.

For a follow-up run (e.g. an ``@hackbot`` mention) we want the agent to operate
on the revision's actual code, not a clean base checkout. When the revision is
stacked on other unlanded revisions, its "actual code" is the whole stack below
it: the base commit the revision records is then its parent's *local* commit,
which never landed and cannot be fetched from the git mirror.

So the checkout is built from the bottom up: check out the last landed commit
underneath the stack, then replay each unlanded ancestor onto it as its own
commit, attributed to whoever wrote that revision — they are this revision's
base, not the agent's work, and the agent will read them in ``git log`` and
``git blame``. The revision's own diff goes on top and is left uncommitted, so
the diff hackbot submits afterwards covers just this revision plus the agent's
follow-up edits.

The agent holds no credentials: every Conduit call goes to the broker sidecar's
read-only proxy (``POST {broker_url}/phabricator/api/<method>``), which
substitutes the real key. Only four read methods are involved, all on the
proxy's allow list — see ``phabricator_proxy.READ_ONLY_METHODS``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from phabricator_client import PhabricatorClient, PhabricatorSettings

from hackbot_runtime import changes

if TYPE_CHECKING:
    from hackbot_runtime.context import HackbotContext

log = logging.getLogger(__name__)

# The broker mounts its read-only Conduit proxy here; PhabricatorClient appends
# `/api/<method>` the same way it would against a real Phabricator instance.
_PROXY_MOUNT = "/phabricator"

# The token the agent sends to the proxy. Not a secret — the proxy discards it
# and substitutes the real Conduit key — but it has to be *a* token, and
# PhabricatorSettings requires the 32 characters a real one has.
_PROXY_API_TOKEN = "hackbot-broker-proxy-placeholder"


class Patch(NamedTuple):
    """One revision's latest diff, with what is needed to commit it as its own.

    ``author`` is ``"Name <email>"`` from the commit the diff was built from, or
    ``None`` when Phabricator has none to give (a diff uploaded through the web
    UI, say).
    """

    revision_id: int
    title: str
    raw_diff: str
    author: str | None

    @property
    def commit_message(self) -> str:
        """A message that says which revision the change came from, and why.

        The revisions below the target get a commit each rather than one
        squashed lump, so that reading `git log` or `git blame` in the checkout
        attributes each change to the revision it came from instead of to
        hackbot.
        """
        return (
            f"D{self.revision_id}: {self.title}\n\n"
            "Replayed by hackbot from Phabricator to rebuild the base of the "
            "revision it was asked to work on. Not the original commit."
        )


class Stack(NamedTuple):
    """Everything needed to reproduce a revision's tree, oldest first.

    ``base_commit`` is a full, fetchable hash: the landed commit underneath the
    whole stack. ``ancestors`` are the unlanded revisions between that commit
    and ``target``, and is empty when the revision is not stacked on anything.
    """

    base_commit: str
    ancestors: list[Patch]
    target: Patch


async def checkout_revision(
    ctx: HackbotContext,
    revision_id: int,
    broker_url: str,
) -> None:
    """Prepare the source so it matches ``D<revision_id>`` before the agent runs.

    Raises :class:`RuntimeError` if the stack cannot be resolved or a diff does
    not apply, so the run fails visibly rather than editing the wrong tree.
    """
    client = PhabricatorClient(
        PhabricatorSettings(
            url=f"{broker_url.rstrip('/')}{_PROXY_MOUNT}",
            api_key=_PROXY_API_TOKEN,
        )
    )
    stack = await _resolve_stack(client, revision_id)

    # Prepare the checkout explicitly at the stack's base commit. Must run
    # before anything else touches the source (prepare_repo raises otherwise).
    repo = await ctx.prepare_repo(ref=stack.base_commit)
    log.info(
        "Checking out D%s (base %s) before running the agent",
        revision_id,
        stack.base_commit,
    )

    if stack.ancestors:
        below = ", ".join(f"D{patch.revision_id}" for patch in stack.ancestors)
        log.info("D%s is stacked on %s; replaying them first", revision_id, below)
        for patch in stack.ancestors:
            _apply(repo, patch)
            # A commit each, attributed to whoever wrote the revision: this is
            # what the agent will read in `git log`/`git blame`, and one squashed
            # lump authored by hackbot would misattribute other people's work.
            # Committing them also takes them out of the agent's own changes —
            # the recorded base moves up to here, so what hackbot submits back
            # covers D<revision_id> alone.
            changes.commit_all(repo, patch.commit_message, author=patch.author)

    _apply(repo, stack.target)

    # Whatever was seeded above is the agent's starting point, not its work.
    # A no-op when nothing was seeded, so this stays unconditional.
    ctx.record_source_base()


async def _resolve_stack(client: PhabricatorClient, revision_id: int) -> Stack:
    """Collect the patches reproducing ``D<revision_id>``, bottom of the stack up."""
    target = await client.search_revision_by_id(revision_id)
    if target is None:
        raise RuntimeError(f"D{revision_id} not found")

    ancestors = await _live_ancestors(client, target)
    ordered = [*ancestors, target]

    base: str | None = None
    patches: list[Patch] = []
    for revision in ordered:
        diff = await client.query_latest_diff(revision["id"])
        if diff is None:
            raise RuntimeError(f"D{revision['id']} has no diffs to apply")
        if base is None:
            # Only the bottom revision's base matters: everything above it is
            # reproduced by replaying diffs, not by checking anything out.
            if not diff.base_commit:
                raise RuntimeError(
                    f"D{revision['id']} records no base commit; nothing to apply onto."
                )
            # Expand it: moz-phab records an abbreviated hash for a repo the
            # size of firefox, and git can only fetch a full object id.
            base = await client.resolve_commit(diff.base_commit)
        patches.append(
            Patch(
                revision_id=revision["id"],
                title=revision["fields"].get("title") or "",
                raw_diff=await client.get_raw_diff(diff.id),
                author=diff.author,
            )
        )

    return Stack(base_commit=base, ancestors=patches[:-1], target=patches[-1])


def _ancestor_phids(stack_graph: dict[str, list[str]], target_phid: str) -> list[str]:
    """Walk a revision's ancestors in ``stackGraph``, direct parent first.

    ``stackGraph`` maps each revision in the stack to its parents, and is part
    of what ``differential.revision.search`` returns. Only a linear chain can be
    replayed as a sequence of patches, so more than one parent is an error; a
    cycle would be Conduit misbehaving, but it costs one ``seen`` set not to
    hang on it.
    """
    ancestors: list[str] = []
    seen = {target_phid}
    current = target_phid
    while True:
        parents = stack_graph.get(current) or []
        if not parents:
            return ancestors
        if len(parents) > 1:
            raise RuntimeError(
                "hackbot cannot reconstruct a non-linear stack: "
                f"{current} has {len(parents)} parents."
            )
        parent = parents[0]
        if parent in seen:
            return ancestors
        seen.add(parent)
        ancestors.append(parent)
        current = parent


async def _live_ancestors(client: PhabricatorClient, target: dict) -> list[dict]:
    """The revisions below ``target`` worth replaying, oldest first.

    Abandoned ancestors are dropped, as are any Conduit does not return (an
    unreadable revision, say) — the same choice ``moz-phab patch`` makes. When
    one in the middle of the stack goes, the remaining diffs will not apply and
    :func:`_apply` reports which revision failed.
    """
    phids = _ancestor_phids(target["fields"]["stackGraph"], target["phid"])
    if not phids:
        return []
    by_phid = {rev["phid"]: rev for rev in await client.search_revisions(phids)}
    return [
        by_phid[phid]
        for phid in reversed(phids)  # root first, direct parent last
        if phid in by_phid and by_phid[phid]["fields"]["status"]["value"] != "abandoned"
    ]


def _apply(repo: Path, patch: Patch) -> None:
    """Apply one revision's diff to ``repo``'s working tree."""
    result = subprocess.run(
        ["git", "-C", str(repo), "apply"],
        input=patch.raw_diff.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        # By far the likeliest cause is a stack whose parent was updated without
        # its children being rebased: each revision's latest diff is applied in
        # order, so a diff written against an older version of the one below it
        # will not match. Nothing hackbot can retry fixes that.
        raise RuntimeError(
            f"Could not apply the diff for D{patch.revision_id}: "
            f"{result.stderr.decode().strip()}. If the revisions below it were "
            "updated without this one being rebased onto them, that stack has "
            "to be rebased before hackbot can reproduce it."
        )
