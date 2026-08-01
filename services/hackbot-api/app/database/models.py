from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    execution_name: Mapped[str | None] = mapped_column(String, nullable=True)
    results_prefix: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifacts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RunAction(Base):
    """A single agent-recorded action from a run's summary.json, and its apply state.

    One row per entry in `summary.json["actions"]`, upserted by the action-applier
    the first time it sees a run so replays (Pub/Sub at-least-once delivery) can
    skip actions already `applied` and only retry `pending`/`failed` ones.
    """

    __tablename__ = "run_actions"
    __table_args__ = (UniqueConstraint("run_id", "idx", name="uq_run_actions_run_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ref: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RunFeedback(Base):
    """A rating of the Bugzilla comment a run posted, from the public feedback link.

    Values for `rating`, `rater_kind`, `dimensions` and `rater_relationship` are
    stored as plain strings and validated by the pydantic enums in app/schemas.py
    (matching how `Run.status` handles `RunStatus`) — the write path is the only
    way rows are created, so a native DB enum would buy little and cost a
    migration every time a dimension is added.

    Dedupe keys differ by rater kind: a signed-in rater keys on their Bugzilla
    id, an anonymous one on a salted hash of IP + user agent. Hence two partial
    unique indexes rather than one constraint — only one of the columns is ever
    set on a given row.
    """

    __tablename__ = "run_feedback"
    __table_args__ = (
        Index(
            "uq_run_feedback_bugzilla_user",
            "run_id",
            "bugzilla_user_id",
            unique=True,
            postgresql_where=text("bugzilla_user_id IS NOT NULL"),
        ),
        Index(
            "uq_run_feedback_anon",
            "run_id",
            "anon_id",
            unique=True,
            postgresql_where=text("anon_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id"), nullable=False, index=True
    )
    rating: Mapped[str] = mapped_column(String, nullable=False)
    dimensions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rater_kind: Mapped[str] = mapped_column(String, nullable=False)
    bugzilla_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bugzilla_name: Mapped[str | None] = mapped_column(String, nullable=True)
    rater_relationship: Mapped[str | None] = mapped_column(String, nullable=True)
    anon_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
