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
from phabricator_client.models import PhabricatorDiff

_FULL_COMMIT_LEN = 40


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

    async def resolve_commit(self, ref: str) -> str | None:
        """Expand a commit identifier to its full 40-char hash, or ``None``.

        A diff's ``sourceControlBaseRevision`` is often abbreviated (moz-phab
        records a short hash for a large repo like firefox), and git can only
        fetch a full object id, not an abbreviation. ``diffusion.querycommits``
        resolves the short hash to the full ``identifier``. Already-full hashes
        are returned as-is without a Conduit call.
        """
        if _is_full_commit(ref):
            return ref
        result = await self.conduit_request("diffusion.querycommits", names=[ref])
        commit_phid = (result.get("identifierMap") or {}).get(ref)
        if not commit_phid:
            return None
        commit = (result.get("data") or {}).get(commit_phid) or {}
        return commit.get("identifier") or None
