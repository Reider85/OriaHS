"""SQLAlchemy 2.x model for the ``tenant_search_stats`` table (ARCHITECT §3.1, PR-10).

Per-tenant search statistics backing adaptive K selection (ARCHITECT §6.4):
``doc_count`` is refreshed daily by ``scripts/update_tenant_stats.py`` while
``avg_query_len`` / ``total_searches`` are updated atomically on every search.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Float, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class TenantSearchStat(Base):
    """Search statistics for one tenant (adaptive K input)."""

    __tablename__ = "tenant_search_stats"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    doc_count: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), nullable=False
    )
    avg_query_len: Mapped[float] = mapped_column(
        Float, server_default=text("0"), nullable=False
    )
    total_searches: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), nullable=False
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
