# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Core data types used across bugbug tools."""

from dataclasses import dataclass


@dataclass
class InlineComment:
    """Represents an inline code review comment."""

    # FIXME: we should drop this class and replace it with a class that
    # represents a comment suggestion instead of a Phabricator inline comment.
    filename: str
    start_line: int
    end_line: int
    content: str
    on_removed_code: bool | None
    id: int | None = None
    date_created: int | None = None
    date_modified: int | None = None
    is_done: bool | None = None
    hunk_start_line: int | None = None
    hunk_end_line: int | None = None
    is_generated: bool | None = None
    explanation: str | None = None
    order: int | None = None


class ReviewRequest:
    """Represents a code review request."""

    patch_id: str

    def __init__(self, patch_id) -> None:
        super().__init__()
        self.patch_id = patch_id
