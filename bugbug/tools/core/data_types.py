# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Core data types used across bugbug tools."""

from dataclasses import dataclass
from enum import StrEnum


class CommentSeverity(StrEnum):
    """How much a review comment matters, from most to least urgent."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class CommentCategory(StrEnum):
    """The kind of problem a review comment reports."""

    SECURITY = "security"
    CORRECTNESS = "correctness"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    STYLE = "style"
    SCOPE = "scope"


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
    severity: CommentSeverity | None = None
    category: CommentCategory | None = None
    details: dict | None = None


class ReviewRequest:
    """Represents a code review request."""

    patch_id: str

    def __init__(self, patch_id) -> None:
        super().__init__()
        self.patch_id = patch_id
