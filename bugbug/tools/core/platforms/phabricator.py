# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Phabricator platform implementation for code review."""

from collections import defaultdict
from datetime import datetime
from functools import cached_property
from logging import getLogger
from typing import Iterable, Optional

import tenacity
from tqdm import tqdm

from bugbug import db, phabricator, utils
from bugbug.tools.core.data_types import InlineComment, ReviewRequest
from bugbug.tools.core.platforms.base import Patch, ReviewData
from bugbug.tools.core.platforms.bugzilla import Bug
from bugbug.utils import get_secret

logger = getLogger(__name__)

# Trusted users group PHID (currently defined as MOCO group members)
MOCO_GROUP_PHID = "PHID-PROJ-57"  # bmo-mozilla-employee-confidential


def _get_users_info_batch_impl(phids: set[str]) -> dict[str, dict]:
    """Internal implementation for fetching user information.

    This function is retried by the wrapper function.
    """
    assert phabricator.PHABRICATOR_API is not None

    if not phids:
        return {}

    logger.info(f"Fetching user info for {len(phids)} PHIDs")

    # Fetch user info (let exceptions propagate for retry)
    users_response = phabricator.PHABRICATOR_API.request(
        "user.search",
        constraints={"phids": list(phids)},
        attachments={"projects": True},
    )

    result = {}
    for user_data in users_response.get("data", []):
        phid = user_data["phid"]
        fields = user_data.get("fields", {})

        project_phids = (
            user_data.get("attachments", {}).get("projects", {}).get("projectPHIDs", [])
        )
        is_trusted = MOCO_GROUP_PHID in project_phids

        result[phid] = {
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
def _get_users_info_batch_with_retry(phids: set[str]) -> dict[str, dict]:
    """Fetch user information with retries."""
    return _get_users_info_batch_impl(phids)


def _get_users_info_batch(phids: set[str]) -> dict[str, dict]:
    """Fetch user information for multiple PHIDs at once.

    Args:
        phids: Set of user PHIDs to fetch

    Returns:
        Dictionary mapping PHID to user info dict with keys:
        - email: User's email address
        - is_trusted: Whether user is trusted (MOCO group only)
        - real_name: User's real name

    Raises:
        Various exceptions from API calls (network errors, etc.)
    """
    return _get_users_info_batch_with_retry(phids)


def _sanitize_comments(comments: list, users_info: dict[str, dict]) -> tuple[list, int]:
    """Sanitize comments by filtering untrusted content.

    Walks comments backwards to find last trusted comment (from MOCO user).
    All content before the last MOCO comment is included (validated by MOCO).
    Content after the last MOCO comment is filtered if not from MOCO.

    Args:
        comments: List of comment objects (sorted by date)
        users_info: Dictionary mapping PHIDs to user info with trust status

    Returns:
        Tuple of (sanitized_comments, filtered_count)

    Security: Fail-closed - untrusted content after last MOCO comment is replaced with placeholder.
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
            # Untrusted comment after last MOCO activity - filter it
            filtered_count += 1
            comment_copy.content = "[Content from untrusted user removed for security]"

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

    def _get_file_from_patch(self, file_path: str, is_before_patch: bool) -> str:
        for changeset in self._changesets:
            if changeset["fields"]["path"]["displayPath"] == file_path:
                break
        else:
            raise FileNotFoundError(f"File {file_path} not found in changesets")

        changeset_id = changeset["id"]

        view = "old" if is_before_patch else "new"
        r = utils.get_session("phabricator_web").get(
            f"https://phabricator.services.mozilla.com/differential/changeset/?view={view}&ref={changeset_id}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        r.raise_for_status()

        return r.text

    def _get_file_from_repo(self, file_path: str, commit_hash: str) -> str:
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{file_path}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )

        if r.status_code == 404:
            raise FileNotFoundError(
                f"File {file_path} not found in commit {commit_hash}"
            )

        r.raise_for_status()
        return r.text

    def get_old_file(self, file_path: str) -> str:
        if file_path.startswith("b/") or file_path.startswith("a/"):
            file_path = file_path[2:]

        try:
            return self._get_file_from_patch(file_path, is_before_patch=True)
        except FileNotFoundError:
            return self._get_file_from_repo(
                file_path, commit_hash=self.base_commit_hash
            )

    @cached_property
    def _changesets(self) -> list[dict]:
        assert phabricator.PHABRICATOR_API is not None

        diff = self._diff_metadata

        changesets = phabricator.PHABRICATOR_API.request(
            "differential.changeset.search",
            constraints={"diffPHIDs": [diff["phid"]]},
        )["data"]

        return changesets

    @cached_property
    def raw_diff(self) -> str:
        assert phabricator.PHABRICATOR_API is not None
        raw_diff = phabricator.PHABRICATOR_API.load_raw_diff(self.diff_id)

        return raw_diff

    @staticmethod
    def _commit_available(commit_hash: str) -> bool:
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/json-rev/{commit_hash}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        return r.ok

    @cached_property
    def _diff_metadata(self) -> dict:
        assert phabricator.PHABRICATOR_API is not None
        diffs = phabricator.PHABRICATOR_API.search_diffs(diff_id=self.diff_id)
        assert len(diffs) == 1
        diff = diffs[0]

        return diff

    @cached_property
    def base_commit_hash(self) -> str:
        diff = self._diff_metadata

        try:
            base_commit_hash = diff["refs"]["base"]["identifier"]
            if self._commit_available(base_commit_hash):
                return base_commit_hash
        except KeyError:
            pass

        end_date = datetime.fromtimestamp(diff["dateCreated"])
        start_date = datetime.fromtimestamp(diff["dateCreated"] - 86400)
        end_date_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
        start_date_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-central/json-pushes?startdate={start_date_str}&enddate={end_date_str}&version=2&tipsonly=1",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
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
        assert phabricator.PHABRICATOR_API is not None

        # We pass either the revision PHID or the revision ID, not both.
        revision_phid = self.revision_phid if not self._revision_id else None

        revision = phabricator.PHABRICATOR_API.load_revision(
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

    def _get_transactions(self) -> list[dict]:
        assert phabricator.PHABRICATOR_API is not None

        transactions = phabricator.PHABRICATOR_API.request(
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
        # TODO: print authors' names instead of PHIDs

        date_format = "%Y-%m-%d %H:%M:%S"
        md_lines = []

        revision = self._revision_metadata
        md_lines.append(f"# Revision D{revision['id']}: {revision['fields']['title']}")
        md_lines.append("")
        md_lines.append("")

        md_lines.append("## Basic Information")
        md_lines.append("")
        md_lines.append(f"- **URI**: {revision['fields']['uri']}")
        md_lines.append(f"- **Revision Author**: {revision['fields']['authorPHID']}")
        md_lines.append(f"- **Title**: {revision['fields']['title']}")
        md_lines.append(f"- **Status**: {revision['fields']['status']['name']}")
        md_lines.append(f"- **Created**: {self.date_created.strftime(date_format)}")
        md_lines.append(f"- **Modified**: {self.date_modified.strftime(date_format)}")
        bug_id = revision["fields"].get("bugzilla.bug-id") or "N/A"
        md_lines.append(f"- **Bugzilla Bug**: {bug_id}")
        md_lines.append("")
        md_lines.append("")

        summary = revision["fields"].get("summary")
        if summary:
            md_lines.append("## Summary")
            md_lines.append("")
            md_lines.append(summary)
            md_lines.append("")
            md_lines.append("")

        test_plan = revision["fields"].get("testPlan")
        if test_plan:
            md_lines.append("## Test Plan")
            md_lines.append("")
            md_lines.append(test_plan)
            md_lines.append("")
            md_lines.append("")

        md_lines.append("## Diff Information")
        diff = self._diff_metadata
        md_lines.append(f"- **Diff ID**: {diff['id']}")
        md_lines.append(f"- **Base Revision**: `{diff['baseRevision']}`")
        if revision["fields"]["authorPHID"] != diff["authorPHID"]:
            md_lines.append(f"- **Diff Author**: {diff['authorPHID']}")
        md_lines.append("")
        md_lines.append("")

        stack_graph = revision["fields"].get("stackGraph")
        if len(stack_graph) > 1:
            md_lines.append("## Stack Information")
            md_lines.append("")
            md_lines.append("**Dependency Graph**:")
            md_lines.append("")
            md_lines.append("```mermaid")
            md_lines.append("graph TD")

            current_phid = revision["phid"]
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

        # Get all comments and sort
        all_comments = sorted(
            # Ignore empty comments
            (comment for comment in self.get_comments() if comment.content.strip()),
            key=lambda c: c.date_created,
        )

        author_phid = revision["fields"]["authorPHID"]
        user_phids = {comment.author_phid for comment in all_comments} | {author_phid}

        users_info = _get_users_info_batch(user_phids)

        # Sanitize comments using pre-step approach
        comments_to_display, filtered_count = _sanitize_comments(
            all_comments, users_info
        )

        for comment in comments_to_display:
            date = datetime.fromtimestamp(comment.date_created)
            date_str = date.strftime(date_format)

            # Get author info (email preferred over PHID)
            author_info = users_info.get(comment.author_phid, {})
            email = author_info.get("email", "Unknown User")
            real_name = author_info.get("real_name")

            if real_name:
                author_display = f"{real_name} ({email})"
            else:
                author_display = email

            if isinstance(comment, PhabricatorInlineComment):
                line_length = comment.line_length
                end_line = comment.end_line
                line_info = (
                    f"Line {comment.start_line}"
                    if line_length == 1
                    else f"Lines {comment.start_line}-{end_line}"
                )
                done_status = " [RESOLVED]" if comment.is_done else ""
                generated_status = " [AI-GENERATED]" if comment.is_generated else ""

                md_lines.append(
                    f"**{date_str}** - **Inline Comment** by {author_display} on `{comment.filename}` "
                    f"at {line_info}{done_status}{generated_status}"
                )
            else:
                md_lines.append(
                    f"**{date_str}** - **General Comment** by {author_display}"
                )

            final_comment_content = comment.content
            divider_index = final_comment_content.find("---")
            if divider_index != -1:
                # Remove footer notes that usually added by reviewbot
                final_comment_content = final_comment_content[:divider_index].strip()

            # Truncate very long comments to avoid overloading the LLM
            if len(final_comment_content) > 2000:
                final_comment_content = (
                    final_comment_content[:2000] + "...\n\n*[Content truncated]*"
                )

            md_lines.append("")
            md_lines.append(final_comment_content)
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

        if not all_comments:
            md_lines.append("*No comments*")
            md_lines.append("")

        return "\n".join(md_lines)


class PhabricatorReviewData(ReviewData):
    def __init__(self):
        super().__init__()
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_review_request_by_id(self, revision_id: int) -> ReviewRequest:
        revisions = phabricator.get(rev_ids=[int(revision_id)])
        assert len(revisions) == 1
        return ReviewRequest(revisions[0]["fields"]["diffID"])

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
