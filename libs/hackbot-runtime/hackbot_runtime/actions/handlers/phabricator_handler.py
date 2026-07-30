"""Apply-side Phabricator actions: submit an already-built diff payload.

``SubmitPatchHandler`` creates a revision and ``UpdatePatchHandler`` adds a diff
to an existing one — one handler per recorded action type, so neither carries
the other's conditionals. What they share (loading the diff artifact, the
``creatediff`` call, the transactions common to both edits) lives in the
module-level helpers above them.

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
# existing WIP prefix, then prepend "WIP: ". Everything hackbot creates is a
# work-in-progress draft; a human promotes it out of WIP when they take it over.
_WIP_PREFIX_RE = re.compile(r"^(?:WIP[: ]|WIP$)", re.IGNORECASE)


def _revision_title(title: str) -> str:
    title = _WIP_PREFIX_RE.sub("", title) or "WIP"
    return f"WIP: {title.strip()}"


async def _revision_fields(revision_id: int) -> dict:
    """The current fields (title/summary/status) of an existing revision."""
    result = await _conduit_request(
        "differential.revision.search", constraints={"ids": [int(revision_id)]}
    )
    data = result.get("data") or []
    return data[0].get("fields", {}) if data else {}


async def _diff_commits(diff_id: Any) -> list[dict]:
    """Return a diff's existing local commits from ``differential.diff.search``."""
    if not diff_id:
        return []

    result = await _conduit_request(
        "differential.diff.search",
        constraints={"ids": [int(diff_id)]},
        attachments={"commits": True},
        limit=1,
    )
    data = result.get("data") or []
    if not data:
        return []

    commits = data[0].get("attachments", {}).get("commits", {}).get("commits", [])
    return commits if isinstance(commits, list) else []


def _preserve_local_commit_authors(
    local_commits: dict, previous_commits: list[dict]
) -> None:
    """Copy the previous diff's commit author identity onto new commit metadata."""
    if not previous_commits:
        return

    previous_author = previous_commits[-1].get("author") or {}
    author_fields = {}
    if previous_author.get("name"):
        author_fields["author"] = previous_author["name"]
    if previous_author.get("email"):
        author_fields["authorEmail"] = previous_author["email"]
    if not author_fields:
        return

    for commit_info in local_commits.values():
        commit_info.update(author_fields)


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
    """Applies ``phabricator.submit_patch``: the diff becomes a new revision.

    Nothing exists on the Phabricator side yet, so the agent's title, summary,
    and bug id are what the revision gets, created in a single edit.
    """

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        bug_id = params["bug_id"]
        summary = params.get("summary")

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

        try:
            diff_result = await _conduit_request(
                "differential.creatediff",
                repositoryPHID=await _repository_phid(),
                **submission["diff"],
            )

            # Reviewers are never assigned by hackbot: a WIP draft gets them at
            # promotion time, and the agent doesn't choose them. A new revision
            # has no status yet, so plan-changes rides this same edit.
            title = _revision_title(params.get("title") or f"Bug {bug_id}")
            transactions: list[dict[str, Any]] = [
                {"type": "update", "value": diff_result["phid"]},
                {"type": "title", "value": title},
                {"type": "bugzilla.bug-id", "value": str(bug_id)},
                {"type": "plan-changes", "value": True},
            ]
            if summary:
                transactions.append({"type": "summary", "value": summary})

            revision_result = await _conduit_request(
                "differential.revision.edit", transactions=transactions
            )
            revision_id = revision_result["object"]["id"]

            # Store commit info on the diff, exactly as moz-phab does *after*
            # creating the revision (so the message can embed the Differential
            # Revision URL). Without this, `moz-phab patch` on the revision
            # fails with "a diff without commit information detected".
            await _set_local_commits(
                diff_result["diffid"],
                submission["local_commits"],
                title,
                summary,
                bug_id,
                revision_id,
            )
        except Exception as exc:
            log.exception("Failed to submit Phabricator diff for bug %s", bug_id)
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {"revision_id": revision_id, "url": _revision_url(revision_id)}
        )


class UpdatePatchHandler:
    """Applies ``phabricator.update_patch``: a new diff on an existing revision.

    Only the diff changes: title, summary, bug id, and review status are left
    exactly as they are, so an update never overwrites something a reviewer or
    the patch author has since edited. The revision's fields are read only to
    rebuild the ``local:commits`` message, which has to match the revision. The
    previous diff's commit author is preserved so updating a patch does not
    silently replace the author's identity with Hackbot's synthetic commit
    identity.
    """

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        revision_id = params["revision_id"]

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

        try:
            fields = await _revision_fields(revision_id)
            previous_diff_id = fields.get("diffID")
            try:
                _preserve_local_commit_authors(
                    submission["local_commits"],
                    await _diff_commits(previous_diff_id),
                )
            except Exception:
                log.warning(
                    "Could not preserve local commit author from previous diff %s",
                    previous_diff_id,
                    exc_info=True,
                )

            diff_result = await _conduit_request(
                "differential.creatediff",
                repositoryPHID=await _repository_phid(),
                **submission["diff"],
            )
            await _conduit_request(
                "differential.revision.edit",
                objectIdentifier=revision_id,
                transactions=[{"type": "update", "value": diff_result["phid"]}],
            )

            await _set_local_commits(
                diff_result["diffid"],
                submission["local_commits"],
                fields.get("title") or f"D{revision_id}",
                fields.get("summary"),
                fields.get("bugzilla.bug-id"),
                revision_id,
            )
        except Exception as exc:
            log.exception("Failed to update Phabricator revision D%s", revision_id)
            return ActionResult.failed(str(exc))

        return ActionResult.ok()
