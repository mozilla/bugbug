"""Check an agent's source tree out at a Phabricator revision, stack included.

For a follow-up run (e.g. an ``@hackbot`` mention) we want the agent to operate
on the revision's actual code, not a clean base checkout. When the revision is
stacked on other unlanded revisions, its "actual code" is the whole stack below
it: the base commit the revision records is then its parent's *local* commit,
which never landed and cannot be fetched from the git mirror.

So the checkout is built from the bottom up: check out the last landed commit
underneath the stack, replay every unlanded ancestor onto it and commit them
(they are this revision's base, not the agent's work), then apply the revision's
own diff on top, uncommitted. What the agent submits afterwards is therefore
just this revision's content plus the agent's follow-up edits.

The agent holds no credentials, so it does not talk to Conduit itself: it drives
moz-phab's Conduit client against the broker sidecar's read-only proxy (``POST
{broker_url}/api/<method>``), and the broker substitutes the real Phabricator
key. moz-phab is used as a library, and only for the Conduit and stack-graph
parts — hackbot drives git itself so it decides exactly which patches become
commits and which stay in the working tree.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from phabricator_client import is_full_commit, select_full_commit

from hackbot_runtime import changes

if TYPE_CHECKING:
    from hackbot_runtime.context import HackbotContext

log = logging.getLogger(__name__)

# Where the broker mounts its read-only Conduit proxy (``phabricator_proxy``).
# A Conduit client treats this as the API root and appends the method name.
_PROXY_PATH = "/phabricator/api/"

# The environment variable moz-phab reads a Conduit token from, ahead of
# ``~/.arcrc``.
_TOKEN_ENV = "MOZPHAB_PHABRICATOR_API_TOKEN"

# The token the agent sends to the broker proxy. Not a secret — the proxy
# discards it and substitutes the real Conduit key — but moz-phab refuses to
# make a call without one, so it has to be something.
_PROXY_API_TOKEN = "api-hackbot-broker-proxy"


class Patch(NamedTuple):
    """One revision's diff, as raw unified-diff text."""

    revision_id: int
    raw_diff: str


class _MozPhab(NamedTuple):
    """The moz-phab pieces the checkout borrows.

    moz-phab is a CLI, not a published library, so what we lean on is named
    here in one place: its Conduit client, its stack-graph walk, and how it
    reads a diff's base commit. The version is pinned exactly by the
    ``[phabricator]`` extra for the same reason.
    """

    conduit: Any
    ancestors_of: Callable[[dict, str], list[str]]
    base_ref: Callable[[dict], str | None]
    non_linear_error: type[Exception]


class Stack(NamedTuple):
    """Everything needed to reproduce a revision's tree, oldest first.

    ``base_commit`` is a full, fetchable hash: the landed commit underneath the
    whole stack. ``ancestors`` are the unlanded revisions between that commit
    and ``target`` (empty for an unstacked revision).
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

    Raises :class:`RuntimeError` if the stack can't be resolved or a diff does
    not apply cleanly — so the run fails visibly rather than editing the wrong
    tree.
    """
    # Imported here, on the main thread, rather than inside the worker below:
    # importing moz-phab installs a SIGINT handler, and `signal.signal` raises
    # anywhere but the main thread.
    mozphab = _load_mozphab()
    stack = await asyncio.to_thread(_resolve_stack, mozphab, revision_id, broker_url)

    # Prepare the checkout explicitly at the stack's base commit. Must run
    # before anything else touches the source (prepare_repo raises otherwise).
    repo = await ctx.prepare_repo(ref=stack.base_commit)

    log.info(
        "Checking out D%s (base %s) before running the agent",
        revision_id,
        stack.base_commit,
    )
    if stack.ancestors:
        stacked_on = ", ".join(f"D{patch.revision_id}" for patch in stack.ancestors)
        log.info("D%s is stacked on %s; replaying them first", revision_id, stacked_on)
        for patch in stack.ancestors:
            _apply(repo, patch)
        # Committing the ancestors takes them out of the agent's changes: the
        # run's recorded base moves to this commit, so the diff hackbot submits
        # back covers only D<revision_id> and the agent's own edits.
        changes.commit_all(repo, f"Stack below D{revision_id}: {stacked_on}")
        ctx.record_source_base()

    _apply(repo, stack.target)


def _apply(repo: Path, patch: Patch) -> None:
    """Apply one revision's diff to ``repo``'s working tree."""
    result = subprocess.run(
        ["git", "-C", str(repo), "apply"],
        input=patch.raw_diff.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not apply diff for D{patch.revision_id}: "
            f"{result.stderr.decode().strip()}"
        )


def _load_mozphab() -> _MozPhab:
    """Import the moz-phab pieces used here. Call this on the main thread."""
    try:
        from mozphab.commands.patch import (
            _get_ancestors_from_stack_graph,
            get_base_ref,
        )
        from mozphab.conduit import conduit
        from mozphab.exceptions import NonLinearException
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "Checking out a Phabricator revision needs moz-phab; install the "
            "hackbot-runtime[phabricator] extra."
        ) from exc
    return _MozPhab(
        conduit=conduit,
        ancestors_of=_get_ancestors_from_stack_graph,
        base_ref=get_base_ref,
        non_linear_error=NonLinearException,
    )


def _resolve_stack(mozphab: _MozPhab, revision_id: int, broker_url: str) -> Stack:
    """Fetch the patches reproducing ``D<revision_id>``, bottom of the stack up.

    Synchronous (moz-phab's Conduit client is), so callers run it in a thread.
    """
    conduit = mozphab.conduit

    with _proxied_conduit(conduit, broker_url):
        revisions = conduit.get_revisions(ids=[revision_id])
        if not revisions:
            raise RuntimeError(f"D{revision_id} not found")
        target = revisions[0]

        try:
            # Returned direct-parent first; we replay oldest first.
            ancestor_phids = mozphab.ancestors_of(
                target["fields"]["stackGraph"], target["phid"]
            )
        except mozphab.non_linear_error as exc:
            raise RuntimeError(
                f"D{revision_id} has more than one parent; hackbot cannot "
                "reconstruct a non-linear stack."
            ) from exc

        ordered = [*_live_ancestors(conduit, ancestor_phids), target]
        diffs = conduit.get_diffs(phids=[rev["fields"]["diffPHID"] for rev in ordered])

        bottom = ordered[0]
        base = mozphab.base_ref(diffs[bottom["fields"]["diffPHID"]])
        if not base:
            raise RuntimeError(
                f"D{bottom['id']} records no base commit; nothing to apply onto."
            )
        base = _resolve_base_commit(conduit, base)

        patches = [
            Patch(
                revision_id=rev["id"],
                raw_diff=conduit.call(
                    "differential.getrawdiff",
                    {"diffID": diffs[rev["fields"]["diffPHID"]]["id"]},
                ),
            )
            for rev in ordered
        ]

    return Stack(base_commit=base, ancestors=patches[:-1], target=patches[-1])


def _live_ancestors(conduit, ancestor_phids: list[str]) -> list[dict]:
    """The ancestor revisions worth replaying, oldest first.

    Abandoned ancestors are dropped, as are ones Conduit did not return at all
    (e.g. restricted access) — matching what ``moz-phab patch`` does. If one in
    the middle of the stack is dropped, the remaining diffs will not apply and
    :func:`_apply` reports which revision failed.
    """
    if not ancestor_phids:
        return []
    by_phid = {rev["phid"]: rev for rev in conduit.get_revisions(phids=ancestor_phids)}
    return [
        by_phid[phid]
        for phid in reversed(ancestor_phids)
        if phid in by_phid and by_phid[phid]["fields"]["status"]["value"] != "abandoned"
    ]


def _resolve_base_commit(conduit, ref: str) -> str:
    """Expand the recorded base commit to a full hash git can fetch.

    moz-phab records an abbreviated hash for a repo the size of firefox, and git
    can only fetch a full object id.
    """
    if is_full_commit(ref):
        return ref
    result = conduit.call("diffusion.querycommits", {"names": [ref]})
    return select_full_commit(ref, result)


class _ProxyRepo:
    """The slice of moz-phab's ``Repository`` that its Conduit client reads.

    Only ``api_url`` is actually used to make a call. Handing over a real
    ``mozphab.git.Git`` instead would drag in an ``.arcconfig`` lookup (the
    checked-out firefox one names the real Phabricator) and moz-phab's
    https-only check, neither of which suits a loopback sidecar.

    Setting ``api_url`` outright is also what lets the broker mount the proxy
    wherever it likes: moz-phab would otherwise derive it as
    ``urljoin(phab_url, "api/")``, which replaces the last path segment and so
    only ever yields ``<host>/api/``.
    """

    def __init__(self, api_url: str) -> None:
        self.api_url = api_url
        self.phab_url = api_url.removesuffix("api/").rstrip("/")


@contextlib.contextmanager
def _proxied_conduit(conduit, broker_url: str) -> Iterator[None]:
    """Point moz-phab's Conduit client at the broker's read-only proxy.

    moz-phab normally takes its API URL from the repository's ``.arcconfig`` and
    its token from ``~/.arcrc``. Neither is right here, so it gets a repository
    stand-in carrying the broker's API URL plus a placeholder token through the
    environment variable it already honours. Both are restored on exit rather
    than left set for the rest of the run.
    """
    previous_repo = getattr(conduit, "repo", None)
    previous_token = os.environ.get(_TOKEN_ENV)
    conduit.set_repo(_ProxyRepo(f"{broker_url.rstrip('/')}{_PROXY_PATH}"))
    os.environ[_TOKEN_ENV] = _PROXY_API_TOKEN
    try:
        yield
    finally:
        conduit.set_repo(previous_repo)
        if previous_token is None:
            os.environ.pop(_TOKEN_ENV, None)
        else:
            os.environ[_TOKEN_ENV] = previous_token
