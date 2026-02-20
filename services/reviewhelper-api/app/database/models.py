from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.enums import (
    ActingCapacity,
    FeedbackType,
    Platform,
    ReviewerStatus,
    ReviewStatus,
)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class ReviewRequest(Base):
    __tablename__ = "review_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
    )

    platform: Mapped[Platform] = mapped_column(Enum(Platform, name="platform_enum"))

    # Phabricator-specific fields
    revision_id: Mapped[int | None]
    diff_id: Mapped[int | None]

    # GitHub-specific fields
    owner: Mapped[str | None]
    repo: Mapped[str | None]
    pr_number: Mapped[int | None]
    sha: Mapped[str | None] = mapped_column(String(40))

    # User info
    user_id: Mapped[int]
    user_name: Mapped[str]
    acting_capacity: Mapped[ActingCapacity | None] = mapped_column(
        Enum(ActingCapacity, name="acting_capacity_enum")
    )
    reviewer_status: Mapped[ReviewerStatus | None] = mapped_column(
        Enum(ReviewerStatus, name="reviewer_status_enum")
    )

    # Status and result
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status_enum"),
        default=ReviewStatus.PENDING,
    )
    details: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    task_id: Mapped[str | None]

    # Relationships
    comments: Mapped[list["GeneratedComment"]] = relationship(
        "GeneratedComment",
        back_populates="review_request",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Partial unique index for Phabricator requests
        Index(
            "ix_review_requests_phabricator_unique",
            "revision_id",
            "diff_id",
            unique=True,
            postgresql_where=(platform == Platform.PHABRICATOR),
        ),
        # Partial unique index for GitHub requests
        Index(
            "ix_review_requests_github_unique",
            "owner",
            "repo",
            "pr_number",
            "sha",
            unique=True,
            postgresql_where=(platform == Platform.GITHUB),
        ),
    )


class GeneratedComment(Base):
    __tablename__ = "generated_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
    )

    review_request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("review_requests.id", ondelete="CASCADE"),
    )

    file_path: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int]
    line_end: Mapped[int]
    on_new: Mapped[bool]
    content: Mapped[str] = mapped_column(Text)

    # ID assigned by the platform after posting the comment
    platform_comment_id: Mapped[int | None] = mapped_column(BigInteger)

    # Additional metadata
    details: Mapped[dict | None] = mapped_column(JSONB)

    # Relationships
    review_request: Mapped[ReviewRequest] = relationship(
        "ReviewRequest", back_populates="comments"
    )
    feedback_items: Mapped[list["Feedback"]] = relationship(
        "Feedback", back_populates="generated_comment"
    )

    __table_args__ = (
        Index("ix_generated_comments_platform_comment_id", "platform_comment_id"),
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
    )

    # Optional FK to generated_comments
    generated_comment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("generated_comments.id", ondelete="CASCADE"),
    )

    feedback_type: Mapped[FeedbackType] = mapped_column(
        Enum(FeedbackType, name="feedback_type_enum")
    )

    user_id: Mapped[int]
    user_name: Mapped[str]
    acting_capacity: Mapped[ActingCapacity | None] = mapped_column(
        Enum(ActingCapacity, name="acting_capacity_enum")
    )
    reviewer_status: Mapped[ReviewerStatus | None] = mapped_column(
        Enum(ReviewerStatus, name="reviewer_status_enum")
    )

    # Relationships
    generated_comment: Mapped[GeneratedComment | None] = relationship(
        "GeneratedComment", back_populates="feedback_items"
    )

    __table_args__ = (
        UniqueConstraint(
            "generated_comment_id",
            "user_id",
            name="uq_feedback_comment_user",
        ),
    )
