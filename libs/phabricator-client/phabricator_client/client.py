"""Small shared Phabricator Conduit client.

A minimal ``httpx``-based Conduit client, deliberately avoiding ``libmozdata``'s
heavier, bulk/futures-oriented client for the handful of lightweight calls
hackbot makes. Shared by the apply-side patch submitter (``hackbot_runtime``)
and the webhook receiver (``hackbot-api``) so there is a single Conduit
implementation.

Config is injected: pass a :class:`PhabricatorSettings`, or let the client load
one from the environment (via ``PhabricatorSettings.from_env``) when none is
provided. The API is asynchronous — both consumers run in an event loop, so the
client is async-native rather than sync-with-threadpool.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from phabricator_client.config import PhabricatorSettings
from phabricator_client.models import PatchStack, PhabricatorDiff, RevisionPatch

_FULL_COMMIT_LEN = 40

# How far down a stack :meth:`PhabricatorClient.get_patch_stack` walks looking
# for a fetchable base. Deep stacks exist, but a walk that long more likely
# means every base is unresolvable, and failing beats collecting diffs forever.
_MAX_STACK_DEPTH = 20


class UnresolvedCommitError(Exception):
    """A commit identifier could not be expanded to a full, fetchable hash."""


class MissingPatchError(Exception):
    """A revision has no diff to check out."""


def _is_full_commit(ref: str) -> bool:
    """True if ``ref`` is a full 40-char lowercase-hex git commit hash."""
    ref = ref.lower()
    return len(ref) == _FULL_COMMIT_LEN and all(c in "0123456789abcdef" for c in ref)


class PhabricatorClient:
    def __init__(self, settings: PhabricatorSettings | None = None) -> None:
        self.settings = settings or PhabricatorSettings.from_env()

    @property
    def base_url(self) -> str:
        return self.settings.url.rstrip("/")

    def revision_url(self, revision_id: int) -> str:
        return f"{self.base_url}/D{revision_id}"

    async def conduit_request(self, method: str, **payload: Any) -> dict:
        """Call a Conduit method, returning its ``result`` (raising on error)."""
        payload["__conduit__"] = {"token": self.settings.api_key}
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/{method}",
                data={"params": json.dumps(payload), "output": "json"},
            )
        response.raise_for_status()
        data = response.json()
        if data.get("error_code"):
            raise RuntimeError(
                f"Conduit error {data['error_code']}: {data.get('error_info')}"
            )
        return data["result"]

    async def search_transactions(self, object_phid: str) -> list[dict]:
        """Return the transactions (comments, status changes, ...) on an object."""
        result = await self.conduit_request(
            "transaction.search", objectIdentifier=object_phid
        )
        return result.get("data") or []

    async def search_revision(self, revision_phid: str) -> dict | None:
        """Return the Differential revision for a PHID, or ``None`` if not found."""
        result = await self.conduit_request(
            "differential.revision.search", constraints={"phids": [revision_phid]}
        )
        data = result.get("data") or []
        return data[0] if data else None

    async def search_revision_by_id(
        self, revision_id: int, *, attachments: dict[str, bool] | None = None
    ) -> dict | None:
        """Return the Differential revision ``D<revision_id>``, or ``None``.

        The id-keyed counterpart of :meth:`search_revision`, for callers that
        start from a revision monogram rather than a PHID. Conduit only returns
        an ``attachments`` block (e.g. ``{"reviewers": True}``) when asked for it.
        """
        payload: dict[str, Any] = {"constraints": {"ids": [revision_id]}}
        if attachments:
            payload["attachments"] = attachments
        result = await self.conduit_request("differential.revision.search", **payload)
        data = result.get("data") or []
        return data[0] if data else None

    async def search_users(self, phids: list[str]) -> dict[str, dict]:
        """Map user PHIDs to ``{"username", "real_name"}`` in one Conduit call.

        Non-user PHIDs are dropped first: a reviewer list mixes users with
        projects (review groups), which ``user.search`` rejects. Unresolvable
        PHIDs are absent from the result.
        """
        wanted = [
            phid
            for phid in dict.fromkeys(phids)  # de-duplicate, keep order
            if phid and phid.startswith("PHID-USER-")
        ]
        if not wanted:
            return {}
        result = await self.conduit_request(
            "user.search", constraints={"phids": wanted}
        )
        return {
            user["phid"]: {
                "username": (user.get("fields") or {}).get("username"),
                "real_name": (user.get("fields") or {}).get("realName"),
            }
            for user in result.get("data") or []
            if user.get("phid")
        }

    async def get_project_members(self, project_phid: str) -> frozenset[str]:
        """Return the user PHIDs belonging to a Phabricator project."""
        result = await self.conduit_request(
            "project.search",
            constraints={"phids": [project_phid]},
            attachments={"members": True},
        )
        members = result["data"][0]["attachments"]["members"]["members"]
        return frozenset(member["phid"] for member in members)

    async def query_latest_diff(self, revision_id: int) -> PhabricatorDiff | None:
        """The most recent diff for a revision, or ``None`` if it has none.

        Uses ``differential.querydiffs`` because, unlike ``diff.search``, it
        exposes ``sourceControlBaseRevision`` (the commit the diff was built on),
        which callers need to reproduce the revision's tree. The result is keyed
        by diff id; the highest id is the latest diff.
        """
        result = await self.conduit_request(
            "differential.querydiffs", revisionIDs=[revision_id]
        )
        if not result:
            return None
        latest = max(result.values(), key=lambda raw: int(raw["id"]))
        return PhabricatorDiff.model_validate(latest)

    async def get_raw_diff(self, diff_id: int) -> str:
        """The raw unified-diff text for a diff (``differential.getrawdiff``)."""
        return await self.conduit_request("differential.getrawdiff", diffID=diff_id)

    async def resolve_commit(self, ref: str) -> str:
        """Expand a commit identifier to its full 40-char hash.

        A diff's ``sourceControlBaseRevision`` is often abbreviated (moz-phab
        records a short hash for a large repo like firefox), and git can only
        fetch a full object id, not an abbreviation. ``diffusion.querycommits``
        resolves the short hash to the full ``identifier``. Already-full hashes
        are returned as-is without a Conduit call.

        Raises :class:`UnresolvedCommitError` when no full hash can be named.
        """
        if _is_full_commit(ref):
            return ref
        result = await self.conduit_request("diffusion.querycommits", names=[ref])
        data = result.get("data")
        commit_phid = result.get("identifierMap").get(ref)
        if commit_phid in data:
            commits = [data[commit_phid]]
        else:
            # ``identifierMap`` only maps unambiguous commits, and a firefox
            # commit is mirrored to autoland, beta, release, etc. Thus, the same
            # hash can appear in multiple repos with different PHIDs.
            commits = data.values()

        identifiers = {
            commit["identifier"]
            for commit in commits
            if commit["identifier"].startswith(ref)
            and _is_full_commit(commit["identifier"])
        }

        if len(identifiers) != 1:
            if identifiers:
                reason = f"it matches {len(identifiers)} commits"
            else:
                reason = (
                    "Diffusion does not know one; the commit may not be "
                    "imported, e.g. an unlanded parent of a stacked patch"
                )
            raise UnresolvedCommitError(
                f"Cannot expand {ref} to a full commit hash: {reason}."
            )

        return identifiers.pop()

    async def get_parent_revision_ids(self, revision_id: int) -> list[int]:
        """The ids of the revisions ``D<revision_id>`` is stacked on top of.

        Phabricator records a stack as ``revision.parent`` edges between
        revisions, so ``edge.search`` names the parents by PHID and a revision
        search turns those back into ids. Empty for a standalone revision or
        the bottom of a stack.
        """
        revision = await self.search_revision_by_id(revision_id)
        if revision is None:
            return []
        result = await self.conduit_request(
            "edge.search",
            sourcePHIDs=[revision["phid"]],
            types=["revision.parent"],
        )
        parent_phids = [
            edge["destinationPHID"]
            for edge in result.get("data") or []
            if edge.get("destinationPHID")
        ]
        if not parent_phids:
            return []
        result = await self.conduit_request(
            "differential.revision.search", constraints={"phids": parent_phids}
        )
        return [parent["id"] for parent in result.get("data") or []]

    async def get_patch_stack(self, revision_id: int) -> PatchStack:
        """A fetchable base commit plus the patches that rebuild a revision.

        Usually that is the revision's own diff on the commit it was built on.
        A stacked revision is built on its parent revision's commit, which only
        exists in the author's local repository: no remote can fetch it, so the
        tree cannot be checked out there. In that case, walk down the stack
        collecting each ancestor's latest diff until a revision whose base does
        resolve, and let the caller replay the collected diffs onto it.

        Raises :class:`MissingPatchError` when a revision on the way down has
        nothing to apply, and :class:`UnresolvedCommitError` when the walk runs
        out of stack (or of parents to choose between) before finding a base.
        """
        patches: list[RevisionPatch] = []
        visited: set[int] = set()
        current = revision_id
        while True:
            visited.add(current)
            diff = await self.query_latest_diff(current)
            if diff is None:
                raise MissingPatchError(f"D{current} has no diffs")
            if not diff.base_commit:
                raise MissingPatchError(f"D{current} diff {diff.id} has no base commit")
            patches.insert(
                0,
                RevisionPatch(
                    revision_id=current,
                    diff_id=diff.id,
                    raw_diff=await self.get_raw_diff(diff.id),
                ),
            )
            try:
                base_commit = await self.resolve_commit(diff.base_commit)
            except UnresolvedCommitError as error:
                current = self._next_in_stack(
                    current,
                    await self.get_parent_revision_ids(current),
                    visited,
                    error,
                )
                continue
            return PatchStack(base_commit=base_commit, patches=patches)

    @staticmethod
    def _next_in_stack(
        revision_id: int,
        parent_ids: list[int],
        visited: set[int],
        error: UnresolvedCommitError,
    ) -> int:
        """The single unvisited parent to continue the walk down a stack with.

        Anything else (no parent, a fork with several parents, a stack deeper
        than :data:`_MAX_STACK_DEPTH`) leaves no one series of patches to
        rebuild, so re-raise the unresolved base that started the walk with the
        reason the walk stopped.
        """
        candidates = [parent_id for parent_id in parent_ids if parent_id not in visited]
        if len(candidates) == 1 and len(visited) < _MAX_STACK_DEPTH:
            return candidates[0]

        if not parent_ids:
            reason = f"D{revision_id} has no parent revision to fall back on"
        elif not candidates:
            reason = f"D{revision_id}'s parent revisions are already in the stack"
        elif len(candidates) > 1:
            reason = (
                f"D{revision_id} has {len(candidates)} parent revisions, so the "
                "patches to apply are ambiguous"
            )
        else:
            reason = (
                f"gave up after walking {_MAX_STACK_DEPTH} revisions down the stack"
            )
        raise UnresolvedCommitError(f"{error} Cannot rebuild the tree: {reason}.")
