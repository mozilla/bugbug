from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text
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
