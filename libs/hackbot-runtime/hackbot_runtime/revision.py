"""Check an agent's source tree out at a Phabricator revision, stack included.

For a follow-up run (e.g. an ``@hackbot`` mention) we want the agent to operate
on the revision's actual code, not a clean base checkout. When the revision is
stacked on other unlanded revisions, its "actual code" is the whole stack below
it: the base commit the revision records is then its parent's *local* commit,
which never landed and cannot be fetched from the git mirror.

The work splits in two:

* hackbot finds the landed commit underneath the *bottom* of the stack and
  shallow-checks the source out there (:func:`_resolve_base`); then
* ``moz-phab patch`` applies the revisions on top, the same way it would on a
  developer's machine (:func:`_apply_revisions`).

The revisions below the target are applied first and committed — they are this
revision's base, not the agent's work — and the target's own diff is then
applied and left uncommitted. So the diff hackbot submits afterwards covers just
this revision plus the agent's follow-up edits.

The agent holds no credentials, so moz-phab never reaches Phabricator directly:
it is pointed at the broker sidecar's read-only Conduit proxy (``POST
{broker_url}/phabricator/api/<method>``), which substitutes the real key.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

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


class StackBase(NamedTuple):
    """Where to start the checkout, and what sits between there and the target.

    ``commit`` is a full, fetchable hash: the landed commit underneath the whole
    stack. ``parent_id`` is the target's direct parent revision, or ``None``
    when the target is not stacked on anything unlanded.
    """

    commit: str
    parent_id: int | None


async def checkout_revision(
    ctx: HackbotContext,
    revision_id: int,
    broker_url: str,
) -> None:
    """Prepare the source so it matches ``D<revision_id>`` before the agent runs.

    Raises :class:`RuntimeError` if the stack cannot be resolved, and lets
    moz-phab's own errors through if a diff does not apply — so the run fails
    visibly rather than editing the wrong tree.
    """
    _load_mozphab()
    base = await asyncio.to_thread(_resolve_base, revision_id, broker_url)

    # Prepare the checkout explicitly at the stack's base commit. Must run
    # before anything else touches the source (prepare_repo raises otherwise).
    repo = await ctx.prepare_repo(ref=base.commit)
    log.info(
        "Checking out D%s (base %s) before running the agent",
        revision_id,
        base.commit,
    )

    await asyncio.to_thread(_apply_revisions, repo, revision_id, base, broker_url)

    # Any revisions below the target are commits now, so re-record the base:
    # they are what the agent starts from, not changes it made.
    ctx.record_source_base()


def _load_mozphab() -> None:
    """Import moz-phab up front, on the main thread.

    Importing it installs a SIGINT handler, and ``signal.signal`` raises
    anywhere but the main thread — so the import must not be left to the worker
    threads that do the actual work. Later imports are cache hits.
    """
    try:
        import mozphab.commands.patch  # noqa: F401
        from mozphab import environment
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "Checking out a Phabricator revision needs moz-phab; install the "
            "hackbot-runtime[phabricator] extra."
        ) from exc

    # There is no terminal in an agent container, and the spinner would only
    # scribble over the run log.
    environment.SHOW_SPINNER = False


def _resolve_base(revision_id: int, broker_url: str) -> StackBase:
    """Find the landed commit underneath ``D<revision_id>``'s stack.

    Uses moz-phab's own stack-graph walk and abandoned filtering, so the bottom
    revision found here is the one ``moz-phab patch`` will also start from.

    Synchronous (moz-phab's Conduit client is), so callers run it in a thread.
    """
    from mozphab.commands.patch import (
        _fetch_and_filter_related,
        _get_ancestors_from_stack_graph,
        get_base_ref,
    )
    from mozphab.conduit import conduit
    from mozphab.exceptions import NonLinearException

    with _conduit_via_proxy(conduit, _ProxyRepo(_proxy_api_url(broker_url))):
        revisions = conduit.get_revisions(ids=[revision_id])
        if not revisions:
            raise RuntimeError(f"D{revision_id} not found")
        target = revisions[0]

        try:
            # Direct parent first, root of the stack last.
            ancestor_phids = _get_ancestors_from_stack_graph(
                target["fields"]["stackGraph"], target["phid"]
            )
        except NonLinearException as exc:
            raise RuntimeError(
                f"D{revision_id} has more than one parent; hackbot cannot "
                "reconstruct a non-linear stack."
            ) from exc

        # Abandoned and inaccessible ancestors drop out here for the same reason
        # moz-phab drops them: it will not apply them either, so the bottom of
        # the stack is whatever survives the filter.
        ancestors, _, related = _fetch_and_filter_related(ancestor_phids, [], False)
        bottom = related[ancestors[-1]] if ancestors else target
        parent_id = related[ancestors[0]]["id"] if ancestors else None

        diff_phid = bottom["fields"]["diffPHID"]
        ref = get_base_ref(conduit.get_diffs(phids=[diff_phid])[diff_phid])
        if not ref:
            raise RuntimeError(
                f"D{bottom['id']} records no base commit; nothing to apply onto."
            )
        return StackBase(commit=_full_commit(conduit, ref), parent_id=parent_id)


def _full_commit(conduit, ref: str) -> str:
    """Expand a recorded base commit to a full hash git can fetch.

    moz-phab records an abbreviated hash for a repo the size of firefox, and git
    can only fetch a full object id.
    """
    if is_full_commit(ref):
        return ref
    return select_full_commit(
        ref, conduit.call("diffusion.querycommits", {"names": [ref]})
    )


def _apply_revisions(
    repo_path: Path,
    revision_id: int,
    base: StackBase,
    broker_url: str,
) -> None:
    """Let ``moz-phab patch`` apply the stack onto the prepared checkout.

    Two passes when the target is stacked: the revisions below it are applied
    and committed together, then the target itself is applied and left in the
    working tree. One pass otherwise.

    Synchronous, so callers run it in a thread.
    """
    from mozphab.conduit import conduit
    from mozphab.git import Git

    # moz-phab reads `user.email` from the *ambient* git config and refuses to
    # run without one, and it copies the environment whenever it builds a git
    # client — so this has to wrap the repo construction as well as the calls.
    with changes.ambient_git_identity():
        repo = Git(str(repo_path))
        _point_at_proxy(repo, broker_url)
        _skip_conduit_probes(repo)

        with _conduit_via_proxy(conduit, repo):
            if base.parent_id is not None:
                _patch(repo, base.parent_id, skip_dependencies=False)
                changes.commit_all(repo_path, f"Revisions below D{revision_id}")
            _patch(repo, revision_id, skip_dependencies=True)


def _patch(repo, revision_id: int, *, skip_dependencies: bool) -> None:
    """Run one ``moz-phab patch`` against the working tree.

    ``--apply-to here`` keeps the checkout hackbot already made; it also skips
    moz-phab's ``check_node``, a bare ``git cat-file`` that would not find the
    base commit in a shallow clone and never fetches it.

    ``--no-commit`` keeps moz-phab out of the business of creating commits,
    which matters for more than tidiness: its commit mode refuses any diff
    without the ``local:commits`` property, which a revision uploaded through
    the Phabricator web UI does not have. hackbot commits the ancestors itself.
    """
    from mozphab.args import parse_args
    from mozphab.commands import patch as patch_command
    from mozphab.exceptions import CommandError, Error, NotFoundError

    argv = [
        "patch",
        f"D{revision_id}",
        "--apply-to",
        "here",
        "--no-commit",
        "--no-branch",
    ]
    if skip_dependencies:
        argv.append("--skip-dependencies")

    args = parse_args(argv)
    repo.set_args(args)
    scope = "on its own" if skip_dependencies else "with the revisions below it"
    log.info("Applying D%s with moz-phab (%s)", revision_id, scope)
    try:
        with _decline_descendants(patch_command):
            patch_command.patch(repo, args)
    except (CommandError, Error, NotFoundError) as exc:
        # moz-phab reports a failed `git apply` as "command 'git' failed to
        # complete successfully", which names neither the revision nor the
        # reason. Say both: by far the likeliest reason is a stack whose parent
        # was updated without its children being rebased, and no amount of
        # retrying fixes that — the author has to rebase.
        what = (
            f"D{revision_id} onto the revisions below it"
            if skip_dependencies
            else f"D{revision_id} or a revision below it"
        )
        raise RuntimeError(
            f"moz-phab could not apply {what}: {exc}. Each revision's latest "
            "diff is applied in order with a plain `git apply`, so a revision "
            "written against an older version of the one below it will not "
            "apply; see the git output above. That stack has to be rebased "
            "before hackbot can reproduce it."
        ) from exc


def _proxy_api_url(broker_url: str) -> str:
    """The broker's Conduit API root."""
    return f"{broker_url.rstrip('/')}{_PROXY_PATH}"


def _point_at_proxy(repo, broker_url: str) -> None:
    """Redirect a moz-phab repository's Conduit URL to the broker's proxy.

    ``Repository.__init__`` takes the URL from the checkout's ``.arcconfig``,
    which for firefox names the real Phabricator, and derives the API URL as
    ``urljoin(phab_url, "api/")`` — that replaces the last path segment, so it
    could only ever yield ``<host>/api/``. Overwriting both afterwards is what
    lets the broker mount the proxy under ``/phabricator``, and it leaves the
    checkout's own ``.arcconfig`` alone: editing that would make the worktree
    dirty, which ``moz-phab patch`` refuses to work on.
    """
    repo.api_url = _proxy_api_url(broker_url)
    repo.phab_url = repo.api_url.removesuffix("api/").rstrip("/")


def _skip_conduit_probes(repo) -> None:
    """Pre-fill the two moz-phab caches whose misses would call Conduit.

    ``moz-phab patch`` opens by pinging Conduit and by asking Phabricator which
    VCS the repository uses (to decide whether git-cinnabar is needed). Neither
    answer is worth having here: an unreachable proxy shows up on the next call
    anyway, and firefox is git on both sides. Writing moz-phab's own cache files
    keeps two more methods off the proxy's allow list, and keeps the VCS lookup
    from depending on a ``repository.callsign`` in the checkout's ``.arcconfig``.
    """
    dot_path = Path(repo.dot_path)
    (dot_path / ".moz-phab_conduit-configured").touch()
    (dot_path / ".moz-phab_vcs_cache").write_text("git")


@contextlib.contextmanager
def _decline_descendants(patch_command) -> Iterator[None]:
    """Answer the one question ``moz-phab patch`` can ask in this configuration.

    Patching the parent to lay down the revisions below the target offers to
    patch the *full* stack, descendants included — which would apply revisions
    the agent was not called on. ``--yes`` answers that question "yes", so it is
    answered here instead. Anything else moz-phab asks is unexpected and would
    otherwise block forever on a stdin nobody is attached to, so it raises.
    """

    def answer(question: str, options: list[str] | None = None) -> str:
        if "full stack" in question:
            return "No"
        raise RuntimeError(f"moz-phab asked for input hackbot cannot give: {question}")

    previous = patch_command.prompt
    patch_command.prompt = answer
    try:
        yield
    finally:
        patch_command.prompt = previous


class _ProxyRepo:
    """The slice of moz-phab's ``Repository`` that its Conduit client reads.

    Used for the base lookup, which happens before there is a checkout to build
    a real ``mozphab.git.Git`` from. Only ``api_url`` is needed to make a call.
    """

    def __init__(self, api_url: str) -> None:
        self.api_url = api_url
        self.phab_url = api_url.removesuffix("api/").rstrip("/")


@contextlib.contextmanager
def _conduit_via_proxy(conduit, repo) -> Iterator[None]:
    """Point moz-phab's Conduit client at the broker's proxy for the duration.

    moz-phab normally takes its API URL from the repository and its token from
    ``~/.arcrc``. Here ``repo`` carries the proxy's URL, and the placeholder
    token goes through the environment variable moz-phab already honours. Both
    are restored on exit rather than left set for the rest of the run.
    """
    previous_repo = getattr(conduit, "repo", None)
    previous_token = os.environ.get(_TOKEN_ENV)
    conduit.set_repo(repo)
    os.environ[_TOKEN_ENV] = _PROXY_API_TOKEN
    try:
        yield
    finally:
        conduit.set_repo(previous_repo)
        if previous_token is None:
            os.environ.pop(_TOKEN_ENV, None)
        else:
            os.environ[_TOKEN_ENV] = previous_token
