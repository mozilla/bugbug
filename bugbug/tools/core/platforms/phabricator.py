# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Phabricator platform implementation for code review."""

import os
from collections import defaultdict
from datetime import datetime
from functools import cache, cached_property
from logging import getLogger
from typing import Iterable, Optional

import tenacity
from async_lru import alru_cache
from tqdm import tqdm

from bugbug.tools.core.connection import get_http_client, get_user_agent
from bugbug.tools.core.data_types import InlineComment
from bugbug.tools.core.platforms.base import Patch, ReviewData
from bugbug.tools.core.platforms.bugzilla import Bug

logger = getLogger(__name__)

# Trusted users group PHID (currently defined as MOCO group members)
MOCO_GROUP_PHID = "PHID-PROJ-a2zxxknk7jm5nw4rtjsl"  # bmo-mozilla-employee-confidential

# Messages used when redacting untrusted content
UNTRUSTED_CONTENT_REDACTED = "[Content from untrusted user removed for security]"
REDACTED_TITLE = "[Unvalidated revision title redacted for security]"
REDACTED_AUTHOR = "[Redacted]"
REDACTED_SUMMARY = "[Unvalidated summary redacted for security]"
REDACTED_TEST_PLAN = "[Unvalidated test plan redacted for security]"


@cache
def get_phabricator_client(
    api_key: Optional[str] = None,
    url: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    """Get a cached Phabricator client instance."""
    from libmozdata.config import set_default_value
    from libmozdata.phabricator import PhabricatorAPI

    # Fallback to old environment variable names for backward compatibility
    if not api_key:
        api_key = os.getenv("PHABRICATOR_KEY") or os.getenv("BUGBUG_PHABRICATOR_TOKEN")

    if not url:
        url = os.getenv("PHABRICATOR_URL") or os.getenv("BUGBUG_PHABRICATOR_URL")

    if not user_agent:
        user_agent = get_user_agent()

    # This is awkward since PhabricatorAPI does not accept user agent directly
    set_default_value("User-Agent", "name", user_agent)

    return PhabricatorAPI(api_key, url)


def _get_users_info_batch_impl(user_phids: set[str]) -> dict[str, dict]:
    """Internal implementation for fetching user information.

    This function is retried by the wrapper function.
    """
    phabricator = get_phabricator_client()

    if not user_phids:
        return {}

    logger.info(f"Fetching user info for {len(user_phids)} PHIDs")

    # Get user names and nick
    users_response = phabricator.request(
        "user.search",
        constraints={"phids": list(user_phids)},
    )

    # Get MOCO group members
    moco_response = phabricator.request(
        "project.search",
        constraints={"phids": [MOCO_GROUP_PHID]},
        attachments={"members": True},
    )

    # Turn it into a set for speed, and perform membership check to determine
    # trusted status.
    moco_members = set()
    if moco_response.get("data"):
        members_data = (
            moco_response["data"][0].get("attachments", {}).get("members", {})
        )
        moco_members = {m["phid"] for m in members_data.get("members", [])}

    result = {}
    for user_data in users_response.get("data", []):
        user_phid = user_data["phid"]
        fields = user_data.get("fields", {})
        is_trusted = user_phid in moco_members

        result[user_phid] = {
            "email": fields.get("username", "unknown"),  # Username is typically email
            "is_trusted": is_trusted,
            "real_name": fields.get("realName", ""),
        }

    return result


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    retry=tenacity.retry_if_exception_type(Exception),
    reraise=True,
)
def _get_users_info_batch_with_retry(user_phids: set[str]) -> dict[str, dict]:
    """Fetch user information with retries."""
    return _get_users_info_batch_impl(user_phids)


def _get_users_info_batch(user_phids: set[str]) -> dict[str, dict]:
    """Fetch user information for multiple user PHIDs at once.

    Args:
        user_phids: Set of user PHIDs to fetch (PHID-USER-xxx)

    Returns:
        Dictionary mapping user PHID to info dict with keys:
        - email: User's email address
        - is_trusted: Whether user is in MOCO group
        - real_name: User's real name
    """
    return _get_users_info_batch_with_retry(user_phids)


def _sanitize_comments(comments: list, users_info: dict[str, dict]) -> tuple[list, int]:
    """Sanitize comments by filtering untrusted content.

    Walks comments backwards to find last trusted comment
    All content before the last trusted comment is included
    Content after the last trusted comment is filtered out

    Args:
        comments: List of comment objects (sorted by date)
        users_info: Dictionary mapping PHIDs to user info with trust status

    Returns:
        Tuple of (sanitized_comments, filtered_count)
    """
    from copy import copy

    # Walk backwards to find last trusted comment (from MOCO)
    last_trusted_index = -1
    for i in range(len(comments) - 1, -1, -1):
        comment_is_trusted = users_info.get(comments[i].author_phid, {}).get(
            "is_trusted", False
        )
        if comment_is_trusted:
            last_trusted_index = i
            break

    # Process comments and apply filtering
    filtered_count = 0
    sanitized_comments = []

    for i, comment in enumerate(comments):
        comment_is_trusted = users_info.get(comment.author_phid, {}).get(
            "is_trusted", False
        )

        # Create a shallow copy to avoid modifying the original
        comment_copy = copy(comment)

        if not comment_is_trusted and i > last_trusted_index:
            # Untrusted comment, redact it and mark explicitly
            filtered_count += 1
            comment_copy.content = UNTRUSTED_CONTENT_REDACTED
            # Mark that this comment's content has been redacted so downstream
            # code doesn't need to rely on string comparisons.
            comment_copy.content_redacted = True

        sanitized_comments.append(comment_copy)

    return sanitized_comments, filtered_count


class PhabricatorComment:
    def __init__(self, transaction: dict):
        comment = transaction["comments"][0]
        self.id: int = comment["id"]
        # TODO: dates should be datetime objects instead of int
        self.date_created: int = comment["dateCreated"]
        self.date_modified: int = comment["dateModified"]
        self.content: str = comment["content"]["raw"]
        self.author_phid: str = transaction["authorPHID"]
        # Whether this comment's content has been redacted due to trust rules.
        # Set by the sanitizer; used by renderers (e.g., to_md()).
        self.content_redacted: bool = False


class PhabricatorGeneralComment(PhabricatorComment):
    """Representation of a general comment posted on a Phabricator revision."""


class PhabricatorInlineComment(PhabricatorComment):
    """Representation of an inline comment posted on a Phabricator revision."""

    def __init__(self, transaction: dict):
        super().__init__(transaction)

        inline_fields = transaction["fields"]
        self.diff_id = inline_fields["diff"]["id"]
        self.filename = inline_fields["path"]
        self.start_line = inline_fields["line"]
        self.line_length = inline_fields["length"]
        self.is_reply = inline_fields["replyToCommentPHID"] is not None
        self.is_done = inline_fields["isDone"]

        # Unfortunately, we do not have this information for a limitation
        # in Phabricator's API.
        self.on_removed_code = None

    @property
    def end_line(self) -> int:
        return self.start_line + self.line_length - 1

    @property
    def is_generated(self) -> bool:
        # This includes comments generated by Review Helper, but excludes any
        # comments that have been edited by the user.
        return (
            "> This comment was generated automatically and has been approved by"
            in self.content
        )


def phabricator_transaction_to_comment(
    transaction: dict,
) -> PhabricatorGeneralComment | PhabricatorInlineComment | None:
    if not transaction.get("comments"):
        return None

    if transaction["type"] == "inline":
        return PhabricatorInlineComment(transaction)

    if transaction["type"] == "comment":
        return PhabricatorGeneralComment(transaction)

    return None


class PhabricatorPatch(Patch):
    def __init__(
        self,
        diff_id: Optional[str | int] = None,
        revision_phid: Optional[str] = None,
        revision_id: Optional[int] = None,
    ) -> None:
        assert diff_id or revision_phid or revision_id, (
            "You must provide at least one of diff_id, revision_phid, or revision_id"
        )

        self._diff_id = diff_id
        self._revision_phid = revision_phid
        self._revision_id = revision_id

    @property
    def patch_url(self) -> str:
        return f"https://phabricator.services.mozilla.com/D{self.revision_id}"

    @property
    def diff_id(self) -> int:
        if self._diff_id:
            return int(self._diff_id)

        if self._revision_id or self._revision_phid:
            return int(self._revision_metadata["fields"]["diffID"])

        raise ValueError("Cannot determine diff ID")

    @property
    def patch_id(self) -> str:
        return str(self.diff_id)

    @property
    def revision_phid(self) -> str:
        if self._revision_phid:
            return self._revision_phid

        if self._revision_id:
            return self._revision_metadata["phid"]

        if self._diff_id:
            return self._diff_metadata["revisionPHID"]

        raise ValueError("Cannot determine revision PHID")

    async def _get_file_from_patch(self, file_path: str, is_before_patch: bool) -> str:
        for changeset in self._changesets:
            if changeset["fields"]["path"]["displayPath"] == file_path:
                break
        else:
            raise FileNotFoundError(f"File {file_path} not found in changesets")

        changeset_id = changeset["id"]

        view = "old" if is_before_patch else "new"
        client = get_http_client()
        r = await client.get(
            f"https://phabricator.services.mozilla.com/differential/changeset/?view={view}&ref={changeset_id}",
        )
        r.raise_for_status()

        return r.text

    async def _get_file_from_repo(self, file_path: str, commit_hash: str) -> str:
        client = get_http_client()
        r = await client.get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{file_path}",
        )

        if r.status_code == 404:
            raise FileNotFoundError(
                f"File {file_path} not found in commit {commit_hash}"
            )

        r.raise_for_status()
        return r.text

    async def get_old_file(self, file_path: str) -> str:
        if file_path.startswith("b/") or file_path.startswith("a/"):
            file_path = file_path[2:]

        try:
            return await self._get_file_from_patch(file_path, is_before_patch=True)
        except FileNotFoundError:
            return await self._get_file_from_repo(
                file_path, commit_hash=await self.get_base_commit_hash()
            )

    @cached_property
    def _changesets(self) -> list[dict]:
        phabricator = get_phabricator_client()

        diff = self._diff_metadata

        changesets = phabricator.request(
            "differential.changeset.search",
            constraints={"diffPHIDs": [diff["phid"]]},
        )["data"]

        return changesets

    @cached_property
    def raw_diff(self) -> str:
        phabricator = get_phabricator_client()
        raw_diff = phabricator.load_raw_diff(self.diff_id)

        return raw_diff

    @staticmethod
    async def _commit_available(commit_hash: str) -> bool:
        client = get_http_client()
        r = await client.get(
            f"https://hg.mozilla.org/mozilla-unified/json-rev/{commit_hash}",
        )
        return r.is_success

    @cached_property
    def _diff_metadata(self) -> dict:
        phabricator = get_phabricator_client()
        diffs = phabricator.search_diffs(diff_id=self.diff_id)
        assert len(diffs) == 1
        diff = diffs[0]

        return diff

    @alru_cache
    async def get_base_commit_hash(self) -> str:
        diff = self._diff_metadata

        try:
            base_commit_hash = diff["refs"]["base"]["identifier"]
            if await self._commit_available(base_commit_hash):
                return base_commit_hash
        except KeyError:
            pass

        end_date = datetime.fromtimestamp(diff["dateCreated"])
        start_date = datetime.fromtimestamp(diff["dateCreated"] - 86400)
        end_date_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
        start_date_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
        client = get_http_client()
        r = await client.get(
            f"https://hg.mozilla.org/mozilla-central/json-pushes?startdate={start_date_str}&enddate={end_date_str}&version=2&tipsonly=1",
        )
        pushes = r.json()["pushes"]
        closest_push = None
        for push_id, push in pushes.items():
            if diff["dateCreated"] - push["date"] < 0:
                continue

            if (
                closest_push is None
                or diff["dateCreated"] - push["date"]
                < diff["dateCreated"] - closest_push["date"]
            ):
                closest_push = push

        assert closest_push is not None
        return closest_push["changesets"][0]

    @property
    def date_created(self) -> datetime:
        return datetime.fromtimestamp(self._diff_metadata["dateCreated"])

    @property
    def date_modified(self) -> datetime:
        return datetime.fromtimestamp(self._diff_metadata["dateModified"])

    @cached_property
    def _revision_metadata(self) -> dict:
        phabricator = get_phabricator_client()

        # We pass either the revision PHID or the revision ID, not both.
        revision_phid = self.revision_phid if not self._revision_id else None

        revision = phabricator.load_revision(
            rev_phid=revision_phid, rev_id=self._revision_id
        )

        return revision

    @cached_property
    def bug(self) -> Bug:
        return Bug.get(self.bug_id)

    @property
    def bug_id(self) -> int:
        return int(self._revision_metadata["fields"]["bugzilla.bug-id"])

    @property
    def bug_title(self) -> str:
        return self.bug.summary

    @cached_property
    def patch_title(self) -> str:
        return self._revision_metadata["fields"]["title"]

    @property
    def patch_description(self) -> str:
        return self._revision_metadata["fields"].get("summary", "")

    @property
    def revision_id(self) -> int:
        return self._revision_metadata["id"]

    @property
    def revision_uri(self) -> str:
        return self._revision_metadata["fields"]["uri"]

    @property
    def revision_status(self) -> str:
        return self._revision_metadata["fields"]["status"]["name"]

    @property
    def author_phid(self) -> str:
        return self._revision_metadata["fields"]["authorPHID"]

    @property
    def diff_author_phid(self) -> str:
        return self._diff_metadata["authorPHID"]

    @property
    def stack_graph(self) -> dict:
        return self._revision_metadata["fields"].get("stackGraph", {})

    @cached_property
    def _all_comments(self) -> list:
        return [c for c in self.get_comments() if c.content.strip()]

    @cached_property
    def _users_info(self) -> dict[str, dict]:
        user_phids = {c.author_phid for c in self._all_comments} | {self.author_phid}
        if self.author_phid != self.diff_author_phid:
            user_phids.add(self.diff_author_phid)
        return _get_users_info_batch(user_phids)

    def _format_user_display(self, user_phid: str) -> str:
        info = self._users_info.get(user_phid, {})
        email = info.get("email", "Unknown")
        real_name = info.get("real_name", "")
        if real_name:
            return f"{real_name} ({email})"
        return email

    @property
    def author(self) -> str:
        return self._format_user_display(self.author_phid)

    @property
    def diff_author(self) -> str | None:
        if self.author_phid == self.diff_author_phid:
            return None
        return self._format_user_display(self.diff_author_phid)

    @property
    def summary(self) -> str | None:
        return self._revision_metadata["fields"].get("summary") or None

    @property
    def test_plan(self) -> str | None:
        return self._revision_metadata["fields"].get("testPlan") or None

    @property
    def comments(self) -> list:
        return sorted(self._all_comments, key=lambda c: c.date_created)

    def _get_transactions(self) -> list[dict]:
        phabricator = get_phabricator_client()

        transactions = phabricator.request(
            "transaction.search",
            objectIdentifier=self._revision_metadata["phid"],
        )["data"]

        return transactions

    def get_comments(
        self,
    ) -> Iterable[PhabricatorInlineComment | PhabricatorGeneralComment]:
        transactions = self._get_transactions()

        for transaction in transactions:
            comment = phabricator_transaction_to_comment(transaction)
            if comment:
                yield comment

    def to_md(self) -> str:
        """Generate a comprehensive, LLM-friendly markdown representation of the patch.

        Returns a well-structured markdown document that includes revision metadata,
        diff information, stack information, code changes, and comments.
        """
        date_format = "%Y-%m-%d %H:%M:%S"
        md_lines = []

        md_lines.append(f"# Revision D{self.revision_id}: {self.patch_title}")
        md_lines.append("")
        md_lines.append("")

        md_lines.append("## Basic Information")
        md_lines.append("")
        md_lines.append(f"- **URI**: {self.revision_uri}")
        md_lines.append(f"- **Revision Author**: {self.author}")
        md_lines.append(f"- **Status**: {self.revision_status}")
        md_lines.append(f"- **Created**: {self.date_created.strftime(date_format)}")
        md_lines.append(f"- **Modified**: {self.date_modified.strftime(date_format)}")
        bug_id = self._revision_metadata["fields"].get("bugzilla.bug-id") or "N/A"
        md_lines.append(f"- **Bugzilla Bug**: {bug_id}")
        md_lines.append("")
        md_lines.append("")

        if self.summary:
            md_lines.append("## Summary")
            md_lines.append("")
            md_lines.append(self.summary)
            md_lines.append("")
            md_lines.append("")

        if self.test_plan:
            md_lines.append("## Test Plan")
            md_lines.append("")
            md_lines.append(self.test_plan)
            md_lines.append("")
            md_lines.append("")

        md_lines.append("## Diff Information")
        diff = self._diff_metadata
        md_lines.append(f"- **Diff ID**: {diff['id']}")
        md_lines.append(f"- **Base Revision**: `{diff['baseRevision']}`")
        if self.diff_author:
            md_lines.append(f"- **Diff Author**: {self.diff_author}")
        md_lines.append("")
        md_lines.append("")

        stack_graph = self.stack_graph
        if len(stack_graph) > 1:
            md_lines.append("## Stack Information")
            md_lines.append("")
            md_lines.append("**Dependency Graph**:")
            md_lines.append("")
            md_lines.append("```mermaid")
            md_lines.append("graph TD")

            current_phid = self._revision_metadata["phid"]
            patch_map = {
                phid: (
                    self
                    if phid == current_phid
                    else PhabricatorPatch(revision_phid=phid)
                )
                for phid in stack_graph.keys()
            }

            for phid, dependencies in stack_graph.items():
                from_patch = patch_map[phid]
                from_id = f"D{from_patch.revision_id}"
                if phid == current_phid:
                    md_lines.append(
                        f"    {from_id}[{from_patch.patch_title} - CURRENT]"
                    )
                    md_lines.append(f"    style {from_id} fill:#105823")
                else:
                    md_lines.append(f"    {from_id}[{from_patch.patch_title}]")

                for dep_phid in dependencies:
                    dep_id = f"D{patch_map[dep_phid].revision_id}"
                    md_lines.append(f"    {from_id} --> {dep_id}")

            md_lines.append("```")
            md_lines.append("")
            md_lines.append("")

        md_lines.append("## Code Changes")
        md_lines.append("")

        try:
            md_lines.append("```diff")
            md_lines.append(self.raw_diff)
            md_lines.append("```")
        except Exception:
            logger.exception("Error while preparing the diff")
            md_lines.append("*Error while preparing the diff*")

        md_lines.append("")
        md_lines.append("")

        md_lines.append("## Comments Timeline")
        md_lines.append("")

        for comment in self.comments:
            date = datetime.fromtimestamp(comment.date_created)
            date_str = date.strftime(date_format)

            if comment.content_redacted:
                comment_author = "[Untrusted User]"
            else:
                comment_author = self._format_user_display(comment.author_phid)

            if isinstance(comment, PhabricatorInlineComment):
                line_info = (
                    f"Line {comment.start_line}"
                    if comment.line_length == 1
                    else f"Lines {comment.start_line}-{comment.end_line}"
                )
                done_status = " [RESOLVED]" if comment.is_done else ""
                generated_status = " [AI-GENERATED]" if comment.is_generated else ""

                md_lines.append(
                    f"**{date_str}** - **Inline Comment** by {comment_author} on `{comment.filename}` "
                    f"at {line_info}{done_status}{generated_status}"
                )
            else:
                md_lines.append(
                    f"**{date_str}** - **General Comment** by {comment_author}"
                )

            final_comment_content = comment.content
            divider_index = final_comment_content.find("---")
            if divider_index != -1:
                final_comment_content = final_comment_content[:divider_index].strip()

            if len(final_comment_content) > 2000:
                final_comment_content = (
                    final_comment_content[:2000] + "...\n\n*[Content truncated]*"
                )

            md_lines.append("")
            md_lines.append(final_comment_content)
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

        if not self._all_comments:
            md_lines.append("*No comments*")
            md_lines.append("")

        return "\n".join(md_lines)


class SanitizedPhabricatorPatch(PhabricatorPatch):
    """A PhabricatorPatch with untrusted content redacted based on trust policy."""

    @cached_property
    def _has_trusted_comment(self) -> bool:
        return any(
            self._users_info.get(c.author_phid, {}).get("is_trusted", False)
            for c in self._all_comments
        )

    @cached_property
    def patch_title(self) -> str:
        if not self._has_trusted_comment:
            return REDACTED_TITLE
        return self._revision_metadata["fields"]["title"]

    @property
    def author(self) -> str:
        if not self._has_trusted_comment:
            return REDACTED_AUTHOR
        return super().author

    @property
    def diff_author(self) -> str | None:
        if self.author_phid == self.diff_author_phid:
            return None
        if not self._has_trusted_comment:
            return REDACTED_AUTHOR
        return super().diff_author

    @property
    def summary(self) -> str | None:
        summary = self._revision_metadata["fields"].get("summary")
        if not summary:
            return None
        if not self._has_trusted_comment:
            return REDACTED_SUMMARY
        return summary

    @property
    def test_plan(self) -> str | None:
        test_plan = self._revision_metadata["fields"].get("testPlan")
        if not test_plan:
            return None
        if not self._has_trusted_comment:
            return REDACTED_TEST_PLAN
        return test_plan

    @cached_property
    def comments(self) -> list:
        sorted_comments = sorted(self._all_comments, key=lambda c: c.date_created)
        sanitized, _ = _sanitize_comments(sorted_comments, self._users_info)
        return sanitized


class PhabricatorReviewData(ReviewData):
    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=2, min=2),
        reraise=True,
    )
    def get_patch_by_id(self, patch_id: str | int) -> Patch:
        return PhabricatorPatch(patch_id)

    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        from bugbug import db, phabricator

        db.download(phabricator.REVISIONS_DB)

        for revision in tqdm(
            phabricator.get_revisions(), total=phabricator.count_revisions()
        ):
            diff_comments: dict[int, list[InlineComment]] = defaultdict(list)

            for transaction in revision["transactions"]:
                if transaction["type"] != "inline":
                    continue

                # Ignore replies
                if transaction["fields"]["replyToCommentPHID"] is not None:
                    continue

                if len(transaction["comments"]) != 1:
                    # Follow up: https://github.com/mozilla/bugbug/issues/4218
                    logger.warning(
                        "Unexpected number of comments in transaction %s",
                        transaction["id"],
                    )
                    continue

                comment = PhabricatorInlineComment(transaction)

                # Ignore reviewbot comments, except the ones generated by Review Helper
                if (
                    transaction["authorPHID"] == "PHID-USER-cje4weq32o3xyuegalpj"
                    and not comment.is_generated
                ):
                    continue

                if not comment_filter(comment):
                    continue

                diff_comments[comment.diff_id].append(
                    InlineComment(
                        filename=comment.filename,
                        start_line=comment.start_line,
                        end_line=comment.end_line,
                        content=comment.content,
                        on_removed_code=comment.on_removed_code,
                        id=comment.id,
                        date_created=comment.date_created,
                        date_modified=comment.date_modified,
                        is_done=comment.is_done,
                        is_generated=comment.is_generated,
                    )
                )

            for diff_id, comments in diff_comments.items():
                yield diff_id, comments
