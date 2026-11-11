# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import ForeignKey, ScalarResult, UniqueConstraint, func, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)


class EvaluationAction(enum.Enum):
    APPROVE = 1
    REJECT = 2
    IGNORE = 3


class DiffStatus(enum.Enum):
    PENDING = 1
    GENERATED = 2
    IGNORED = 3
    SUBMITTED = 4
    REPLACED = 5


class IgnoreReasons(enum.Enum):
    SIZE = 1


class SuggestionIgnoreReason(enum.Enum):
    NOT_SURE = 1
    TRIVIAL = 2
    DEVELOPMENT_PHASE = 3
    OTHER = 4
    REVIEW_TIP = 5
    INCORRECT = 6
    VALID_REDUNDANT = 7


class ReviewRequestMode(enum.Enum):
    NORMAL = 1
    EXPERIMENTAL = 2


class Base(DeclarativeBase):
    pass


class ReviewRequest(Base):
    __tablename__ = "review_requests"
    __table_args__ = (UniqueConstraint("diff_id", "mode", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    diff_id: Mapped[int] = mapped_column(index=True)
    revision_id: Mapped[int] = mapped_column(index=True)
    status: Mapped[DiffStatus]
    suggestions: Mapped[List["Suggestion"]] = relationship(
        back_populates="review_request"
    )
    bugbug_version: Mapped[str]
    tool_variant: Mapped[Optional[str]]
    ignore_reason: Mapped[Optional[IgnoreReasons]]
    mode: Mapped[ReviewRequestMode]
    sequence: Mapped[int]

    # pylint:disable=not-callable
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), server_onupdate=func.now()
    )

    @property
    def is_recently_created(self):
        return (datetime.utcnow() - self.created_at).total_seconds() < 240

    def has_evaluation(self, session: Session) -> bool:
        if self.status == DiffStatus.IGNORED:
            # If the diff is ignored, there's no way it has evaluation. We don't
            # need to query the database.
            return False

        stmt = (
            select(Evaluation.id)
            .join(Suggestion)
            .where(Suggestion.review_request_id == self.id)
            .limit(1)
        )
        return session.scalar(stmt) is not None

    def is_replaced(self, session: Session) -> bool:
        if self.status == DiffStatus.REPLACED:
            return True

        stmt = select(ReviewRequest.status).where(ReviewRequest.id == self.id)

        return session.scalar(stmt) == DiffStatus.REPLACED

    def suggestions_with_their_evaluation(
        self, session: Session
    ) -> ScalarResult["Suggestion"]:
        stmt = (
            select(Suggestion)
            .options(selectinload(Suggestion.evaluation))
            .where(Suggestion.review_request_id == self.id)
        )
        return session.scalars(stmt)


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_request_id: Mapped[int] = mapped_column(ForeignKey(ReviewRequest.id))
    review_request: Mapped[ReviewRequest] = relationship(back_populates="suggestions")
    file_path: Mapped[str]
    line_start: Mapped[int]
    line_end: Mapped[int]
    has_added_lines: Mapped[bool]
    content: Mapped[str]
    evaluation: Mapped[Optional["Evaluation"]] = relationship(
        back_populates="suggestion"
    )

    # TODO: Fill the columns for old suggestions, then change the column
    # to NOT NULL.
    llm_name: Mapped[Optional[str]]
    llm_temperature: Mapped[Optional[float]]

    # This will be filled once the comment is posted to Phabricator. It will
    # store the inline comment ID returned by the Phabricator Conduit API.
    inline_comment_id: Mapped[Optional[int]]

    # pylint:disable=not-callable
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), server_onupdate=func.now()
    )

    @property
    def final_line_start(self):
        """Return the final line start based on evaluation if present."""
        evaluation = self.evaluation
        return (
            self.line_start
            if evaluation is None or evaluation.line_start is None
            else evaluation.line_start
        )

    @property
    def final_line_end(self):
        """Return the final line end based on evaluation if present."""
        evaluation = self.evaluation
        return (
            self.line_end
            if evaluation is None or evaluation.line_end is None
            else evaluation.line_end
        )

    @property
    def final_line_length(self):
        """Return the final line length based on evaluation if present."""
        return self.final_line_end - self.final_line_start


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(ForeignKey(Suggestion.id), unique=True)
    suggestion: Mapped[Suggestion] = relationship(back_populates="evaluation")
    user: Mapped[str]
    action: Mapped[EvaluationAction]
    edited_comment: Mapped[Optional[str]]
    line_start: Mapped[Optional[int]]
    line_end: Mapped[Optional[int]]
    is_latest_diff: Mapped[bool]
    ignore_reason: Mapped[Optional[SuggestionIgnoreReason]]

    # pylint:disable=not-callable
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
