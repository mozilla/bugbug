"""Apply-side Phabricator action: submits an already-built diff payload.

Pairs with the recording side in ``actions/phabricator.py`` and the payload
built agent-side in ``hackbot_runtime.changes.build_phabricator_diff`` (while
the agent still has its own checkout — nothing here ever touches git, a
local repo, or ``moz-phab``). Talks to Phabricator's Conduit API directly
with a small ``requests``-based client, mirroring ``bugzilla_handler.py``'s
choice to avoid ``libmozdata``'s heavier, bulk/futures-oriented client for a
single lightweight call.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

import requests

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_DEFAULT_PHABRICATOR_URL = "https://phabricator.services.mozilla.com"
_DIFF_ARTIFACT_KEY = "changes/phabricator_diff.json"
_TIMEOUT_SECONDS = 60


def _base_url() -> str:
    return os.environ.get("PHABRICATOR_URL", _DEFAULT_PHABRICATOR_URL).rstrip("/")


def _revision_url(revision_id: int) -> str:
    return f"{_base_url()}/D{revision_id}"


def _api_key() -> str:
    token = os.environ.get("PHABRICATOR_API_KEY", "")
    if not token:
        raise RuntimeError("PHABRICATOR_API_KEY is not configured")
    return token


def _conduit_request(method: str, **payload: Any) -> dict:
    payload["__conduit__"] = {"token": _api_key()}
    response = requests.post(
        f"{_base_url()}/api/{method}",
        data={"params": json.dumps(payload), "output": "json"},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error_code"):
        raise RuntimeError(
            f"Conduit error {data['error_code']}: {data.get('error_info')}"
        )
    return data["result"]


@lru_cache(maxsize=1)
def _repository_phid() -> str:
    """The target repository's PHID, needed on every `differential.creatediff` call.

    Prefers an explicit `PHABRICATOR_REPOSITORY_PHID` (simplest, most robust —
    the recommended way to configure this in production) and falls back to a
    `diffusion.repository.search` lookup by short name
    (`PHABRICATOR_REPOSITORY_NAME`, default "mozilla-central") otherwise.
    """
    configured = os.environ.get("PHABRICATOR_REPOSITORY_PHID")
    if configured:
        return configured

    name = os.environ.get("PHABRICATOR_REPOSITORY_NAME", "mozilla-central")
    result = _conduit_request("diffusion.repository.search")
    for repository in result.get("data", []):
        fields = repository.get("fields", {})
        if fields.get("shortName") == name or fields.get("name") == name:
            return repository["phid"]
    raise RuntimeError(f"Could not find a Phabricator repository named '{name}'")


class SubmitPatchHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        bug_id = params["bug_id"]
        revision_id = params.get("revision_id")

        try:
            raw = await ctx.download_artifact(_DIFF_ARTIFACT_KEY)
            diff_payload = json.loads(raw)
        except Exception as exc:
            log.exception(
                "Failed to load Phabricator diff artifact for run %s", ctx.run_id
            )
            return ActionResult.failed(
                f"No Phabricator diff artifact for this run: {exc}"
            )

        try:
            diff_result = _conduit_request(
                "differential.creatediff",
                repositoryPHID=_repository_phid(),
                **diff_payload,
            )
            diff_phid = diff_result["phid"]

            transactions: list[dict[str, Any]] = [
                {"type": "update", "value": diff_phid}
            ]
            if params.get("title"):
                transactions.append({"type": "title", "value": params["title"]})
            if params.get("summary"):
                transactions.append({"type": "summary", "value": params["summary"]})
            if params.get("reviewers"):
                # Assumes Phabricator resolves these identifiers directly;
                # if Mozilla's instance requires PHIDs instead of usernames
                # for this transaction, a user.search-based resolution step
                # needs adding here — not verified against a live instance.
                transactions.append(
                    {"type": "reviewers.add", "value": params["reviewers"]}
                )
            transactions.append({"type": "bugzilla.bug-id", "value": str(bug_id)})

            edit_args: dict[str, Any] = {"transactions": transactions}
            if revision_id:
                edit_args["objectIdentifier"] = revision_id
            revision_result = _conduit_request(
                "differential.revision.edit", **edit_args
            )
        except Exception as exc:
            log.exception("Failed to submit Phabricator diff for bug %s", bug_id)
            return ActionResult.failed(str(exc))

        object_data = revision_result.get("object") or {}
        new_revision_id = object_data.get("id") or revision_id
        return ActionResult.ok(
            {
                "revision_id": new_revision_id,
                "revision_url": (
                    _revision_url(new_revision_id) if new_revision_id else None
                ),
            }
        )
