# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Base classes for code review platforms."""

import re
from abc import ABC, abstractmethod
from datetime import datetime
from functools import cached_property
from typing import Iterable

from unidiff import Hunk, PatchedFile, PatchSet

from bugbug.tools.core.data_types import InlineComment


class Patch(ABC):
    """Abstract base class for code patches."""

    @property
    @abstractmethod
    def patch_id(self) -> str: ...

    @property
    @abstractmethod
    def raw_diff(self) -> str: ...

    @property
    @abstractmethod
    def date_created(self) -> datetime: ...

    @cached_property
    def patch_set(self) -> PatchSet:
        return PatchSet.from_string(self.raw_diff)

    @property
    @abstractmethod
    def bug_title(self) -> str:
        """Return the title of the bug associated with this patch."""
        ...

    @property
    @abstractmethod
    def has_bug(self) -> bool:
        """Return whether this patch is associated with a bug."""
        ...

    @property
    @abstractmethod
    def patch_title(self) -> str:
        """Return the title of the patch."""
        ...

    @property
    @abstractmethod
    def patch_description(self) -> str:
        """Return the description of the patch."""
        ...

    @property
    @abstractmethod
    def patch_url(self) -> str:
        """Return the URL of the patch."""
        ...

    @abstractmethod
    async def get_old_file(self, file_path: str) -> str:
        """Return the contents of a file before the patch was applied."""
        ...


class ReviewData(ABC):
    """Abstract base class for code review platform data access."""

    NIT_PATTERN = re.compile(r"[^a-zA-Z0-9]nit[\s:,]", re.IGNORECASE)

    @abstractmethod
    def get_patch_by_id(self, patch_id: str | int) -> Patch:
        raise NotImplementedError

    @abstractmethod
    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        raise NotImplementedError

    def load_raw_diff_by_id(self, diff_id) -> str:
        """Load a patch from local cache if it exists.

        If the patch is not in the local cache it will be requested from the
        provider and cache it locally.

        Args:
            diff_id: The ID of the patch.

        Returns:
            The patch.
        """
        try:
            with open(f"patches/{diff_id}.patch", "r") as f:
                raw_diff = f.read()
        except FileNotFoundError:
            with open(f"patches/{diff_id}.patch", "w") as f:
                patch = self.get_patch_by_id(diff_id)
                raw_diff = patch.raw_diff
                f.write(raw_diff)

        return raw_diff

    def get_matching_hunk(
        self, patched_file: PatchedFile, comment: InlineComment
    ) -> Hunk:
        def source_end(hunk: Hunk) -> int:
            return hunk.source_start + hunk.source_length

        def target_end(hunk: Hunk) -> int:
            return hunk.target_start + hunk.target_length

        if comment.on_removed_code is None:
            matching_hunks = [
                hunk
                for hunk in patched_file
                if hunk.target_start <= comment.start_line < target_end(hunk)
                or hunk.source_start <= comment.start_line < source_end(hunk)
            ]

            # If there is more than one matching hunk, choose the one where the
            # line number of the comment corresponds to an added or deleted line. We
            # prioritize added lines over deleted lines because comments are more
            # likely to be on added lines than deleted lines.
            if len(matching_hunks) > 1:
                from logging import getLogger

                logger = getLogger(__name__)
                logger.warning(
                    "Multiple matching hunks found for comment %s in file %s",
                    comment.id,
                    comment.filename,
                )
                for hunk in matching_hunks:
                    for line in hunk:
                        if line.is_added and line.target_line_no == comment.start_line:
                            return hunk

                    for line in hunk:
                        if (
                            line.is_removed
                            and line.source_line_no == comment.start_line
                        ):
                            return hunk

            if len(matching_hunks) != 0:
                return matching_hunks[0]

        elif comment.on_removed_code:
            for hunk in patched_file:
                if hunk.source_start <= comment.start_line < source_end(hunk):
                    return hunk

        else:
            for hunk in patched_file:
                if hunk.target_start <= comment.start_line < target_end(hunk):
                    return hunk

    def retrieve_comments_with_hunks(self):
        def comment_filter(comment: InlineComment):
            # We want to keep all generated comments
            if comment.is_generated:
                return True

            comment_content = comment.content

            # Ignore very short and very long comments
            if not 50 < len(comment_content) < 500:
                return False

            # Ignore comments with URLs
            if "https://" in comment_content or "http://" in comment_content:
                return False

            #  Ignore nit comments
            if self.NIT_PATTERN.search(comment_content):
                return False

            # Ignore comments with code blocks
            if "```" in comment_content:
                return False

            comment_lower = comment_content.lower()
            if any(
                phrase in comment_lower
                for phrase in [
                    "wdyt?",
                    "what do you think?",
                    "you explain",
                    "understand",
                ]
            ):
                return False

            return True

        from logging import getLogger

        from libmozdata.phabricator import ConduitError
        from unidiff.errors import UnidiffParseError

        logger = getLogger(__name__)

        for diff_id, comments in self.get_all_inline_comments(comment_filter):
            try:
                patch_set = PatchSet.from_string(self.load_raw_diff_by_id(diff_id))
            except UnidiffParseError:
                # TODO: use log instead of print
                print(f"Failed to parse {diff_id}")
                continue
            except ConduitError:
                logger.warning("Failed to load %d", diff_id)
                continue

            file_map = {
                patched_file.path: patched_file
                for patched_file in patch_set
                if patched_file.is_modified_file
            }
            for comment in comments:
                patched_file = file_map.get(comment.filename)
                if not patched_file:
                    continue

                hunk = self.get_matching_hunk(patched_file, comment)
                if not hunk:
                    continue

                yield hunk, comment
