"""SQLAlchemy 2.x model for the ``search_outbox`` table (ARCHITECT §3.1, P-02)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class OutboxItem(Base):
    """Dual-write recovery queue: pending Qdrant syncs per document."""

    __tablename__ = "search_outbox"
    __table_args__ = (
        CheckConstraint("op IN ('upsert','delete')", name="op_valid"),
        CheckConstraint(
            "status IN ('pending','in_progress','done','failed','dead')",
            name="status_valid",
        ),
        Index(
            "search_outbox_pending_idx",
            "next_retry_at",
            postgresql_where=text("status IN ('pending','failed')"),
        ),
        Index("search_outbox_doc_idx", "document_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id"),
    )
    op: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    content_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
