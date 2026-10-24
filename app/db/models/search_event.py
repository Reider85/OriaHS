"""SQLAlchemy 2.x model for the ``search_events`` table (ARCHITECT §3.1, PR-02).

Search events store user search interactions for analytics and personalization.
Table is partitioned by created_at (daily) for efficient archival and querying.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import BIGINT
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class SearchEvent(Base):
    """User search event for analytics and personalization."""

    __tablename__ = "search_events"
    __table_args__ = (
        Index("ix_search_events_user_time_idx", "user_id", "created_at"),
        Index("ix_search_events_exp_idx", "experiment_id", "created_at"),
    )

    # Primary key (BIGSERIAL in SQL, mapped as BIGINT auto-increment)
    id: Mapped[int] = mapped_column(BIGINT, autoincrement=True, primary_key=True)

    # User and tenant context
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    # Search query information
    query_text: Mapped[str] = mapped_column(nullable=False)
    query_lang: Mapped[str | None] = mapped_column(nullable=True)

    # Search results and interaction
    top_doc_ids: Mapped[list[UUID]] = mapped_column(
        PG_ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    clicked_doc_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    position: Mapped[int | None] = mapped_column(nullable=True)

    # Search metadata
    fusion_strategy: Mapped[str | None] = mapped_column(nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(nullable=True)
    variant: Mapped[str | None] = mapped_column(
        nullable=True, server_default="control"
    )  # A/B variant (PR-03), shadow traffic (PR-08)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"SearchEvent(id={self.id}, user_id={self.user_id}, query='{self.query_text[:50]}...')"
        )
