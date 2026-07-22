"""Apply-side Phabricator action: submits an already-built diff payload.

Pairs with the recording side in ``actions/phabricator.py`` and the payload
built agent-side in ``hackbot_runtime.changes.build_phabricator_diff`` (while
the agent still has its own checkout — nothing here ever touches git, a
local repo, or ``moz-phab``). Talks to Phabricator's Conduit API through the
shared ``phabricator_client`` lib, a small ``httpx``-based client that avoids
``libmozdata``'s heavier, bulk/futures-oriented client for these single
lightweight calls.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any

from async_lru import alru_cache
from phabricator_client import PhabricatorClient

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_DIFF_ARTIFACT_KEY = "changes/phabricator_diff.json"


@lru_cache(maxsize=1)
def _client() -> PhabricatorClient:
    return PhabricatorClient()


async def _conduit_request(method: str, **payload: Any) -> dict:
    return await _client().conduit_request(method, **payload)


def _revision_url(revision_id: int) -> str:
    return _client().revision_url(revision_id)


@alru_cache(maxsize=1)
async def _repository_phid() -> str:
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
    result = await _conduit_request("diffusion.repository.search")
    for repository in result.get("data", []):
        fields = repository.get("fields", {})
        if fields.get("shortName") == name or fields.get("name") == name:
            return repository["phid"]
    raise RuntimeError(f"Could not find a Phabricator repository named '{name}'")


# moz-phab's arc commit-message template (see mozphab.commits) — replicated so
# the local:commits message we store matches what moz-phab itself would write.
_ARC_COMMIT_MESSAGE_TEMPLATE = """
{title}

Summary:
{body}

Test Plan:
{test_plan}

Reviewers: {reviewers}

Subscribers:

Bug #: {bug_id}
""".strip()


# Mirrors moz-phab's WIP_RE / revision_title (mozphab.commits): strip any
# existing WIP prefix, then prepend "WIP: " for a work-in-progress revision.
_WIP_PREFIX_RE = re.compile(r"^(?:WIP[: ]|WIP$)", re.IGNORECASE)


def _revision_title(title: str, wip: bool) -> str:
    title = _WIP_PREFIX_RE.sub("", title) or "WIP"
    title = title.strip()
    return f"WIP: {title}" if wip else title


async def _revision_fields(revision_id: int) -> dict:
    """The current fields (title/summary/status) of an existing revision."""
    result = await _conduit_request(
        "differential.revision.search", constraints={"ids": [int(revision_id)]}
    )
    data = result.get("data") or []
    return data[0].get("fields", {}) if data else {}


def _arc_commit_message(title: str, summary: str | None, bug_id: Any, url: str) -> str:
    """Build moz-phab's arc commit message, with the Differential Revision URL.

    Mirrors ``Commit.build_arc_commit_message`` + ``amend_revision_url`` so the
    reconstructed commit reads identically to a moz-phab submission. Reviewers
    are always empty: hackbot never assigns them (WIP submissions omit them).
    """
    body = summary or ""
    if body:
        body += "\n"
    body += f"\nDifferential Revision: {url}"
    return _ARC_COMMIT_MESSAGE_TEMPLATE.format(
        title=title,
        body=body,
        test_plan="",
        reviewers="",
        bug_id=bug_id if bug_id is not None else "",
    )


async def _set_local_commits(
    diff_id: Any,
    local_commits: dict,
    title: str,
    summary: str | None,
    bug_id: Any,
    revision_id: int,
) -> None:
    """Complete and store moz-phab's ``local:commits`` diff property.

    The git-derived fields (author/time/tree/parents/node) come from the
    agent-built artifact; ``summary`` (the resolved, possibly ``WIP:``-prefixed
    revision title) and the arc-formatted ``message`` are filled in here, since
    they need the revision URL.
    """
    message = _arc_commit_message(title, summary, bug_id, _revision_url(revision_id))
    for commit_info in local_commits.values():
        commit_info["summary"] = title
        commit_info["message"] = message

    await _conduit_request(
        "differential.setdiffproperty",
        diff_id=diff_id,
        name="local:commits",
        data=json.dumps(local_commits),
    )


class AddCommentHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        revision_id = params["revision_id"]
        try:
            await _conduit_request(
                "differential.revision.edit",
                objectIdentifier=revision_id,
                transactions=[{"type": "comment", "value": params["text"]}],
            )
        except Exception as exc:
            log.exception("Failed to comment on revision D%s", revision_id)
            return ActionResult.failed(str(exc))
        return ActionResult.ok(
            {"revision_id": revision_id, "revision_url": _revision_url(revision_id)}
        )


class SubmitPatchHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        bug_id = params["bug_id"]
        revision_id = params.get("revision_id")

        try:
            raw = await ctx.download_artifact(_DIFF_ARTIFACT_KEY)
            submission = json.loads(raw)
        except Exception as exc:
            log.exception(
                "Failed to load Phabricator submission artifact for run %s", ctx.run_id
            )
            return ActionResult.failed(
                f"No Phabricator submission artifact for this run: {exc}"
            )

        # The creatediff payload plus the git side of the local:commits property
        # (completed and stored after the revision edit). Tolerate a bare diff
        # payload without the wrapper too.
        diff_payload = submission.get("diff", submission)
        local_commits = submission.get("local_commits")

        wip = params.get("wip", True)

        try:
            diff_result = await _conduit_request(
                "differential.creatediff",
                repositoryPHID=await _repository_phid(),
                **diff_payload,
            )
            diff_phid = diff_result["phid"]

            # Resolve title/summary (and, for updates, the current status) once;
            # reused for the transactions and the local:commits property.
            raw_title = params.get("title")
            raw_summary = params.get("summary")
            existing_status = None
            if revision_id:
                fields = await _revision_fields(revision_id)
                existing_status = (fields.get("status") or {}).get("value")
                if not raw_title:
                    raw_title = fields.get("title")
                if raw_summary is None:
                    raw_summary = fields.get("summary")
            title = _revision_title(raw_title or f"Bug {bug_id}", wip)

            # Reviewers are never assigned by hackbot: a WIP draft gets them at
            # promotion time, and the agent doesn't choose them.
            transactions: list[dict[str, Any]] = [
                {"type": "update", "value": diff_phid},
                {"type": "title", "value": title},
            ]
            if raw_summary:
                transactions.append({"type": "summary", "value": raw_summary})
            transactions.append({"type": "bugzilla.bug-id", "value": str(bug_id)})

            # Mark WIP via a `plan-changes` transaction, mirroring moz-phab. If
            # the revision is already `changes-planned`, Phabricator errors on a
            # no-op status change, so send it in a separate follow-up edit.
            post_transactions: list[dict[str, Any]] = []
            if wip:
                plan_changes = {"type": "plan-changes", "value": True}
                if existing_status == "changes-planned":
                    post_transactions.append(plan_changes)
                else:
                    transactions.append(plan_changes)
            elif existing_status and existing_status not in (
                "needs-review",
                "accepted",
            ):
                transactions.append({"type": "request-review", "value": True})

            edit_args: dict[str, Any] = {"transactions": transactions}
            if revision_id:
                edit_args["objectIdentifier"] = revision_id
            revision_result = await _conduit_request(
                "differential.revision.edit", **edit_args
            )

            object_data = revision_result.get("object") or {}
            new_revision_id = object_data.get("id") or revision_id

            if post_transactions and new_revision_id:
                await _conduit_request(
                    "differential.revision.edit",
                    objectIdentifier=new_revision_id,
                    transactions=post_transactions,
                )

            # Store commit info on the diff, exactly as moz-phab does *after*
            # creating the revision (so the message can embed the Differential
            # Revision URL). Without this, `moz-phab patch` on the revision
            # fails with "a diff without commit information detected".
            if local_commits and new_revision_id:
                await _set_local_commits(
                    diff_result["diffid"],
                    local_commits,
                    title,
                    raw_summary,
                    bug_id,
                    new_revision_id,
                )
        except Exception as exc:
            log.exception("Failed to submit Phabricator diff for bug %s", bug_id)
            return ActionResult.failed(str(exc))

        revision_url = _revision_url(new_revision_id) if new_revision_id else None
        return ActionResult.ok(
            {
                "revision_id": new_revision_id,
                "revision_url": revision_url,
                "url": revision_url,
            }
        )
