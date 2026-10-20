"""SQLAlchemy 2.x model for the ``ab_experiments`` table (ARCHITECT §11.3, PR-03).

Experiment definitions for the A/B framework. ``variants`` is a JSONB mapping
variant name -> parameter overlay (``fusion``, ``fusion_alpha``, ``rerank``,
``personalize``, ``diversify`` — all optional); ``weights`` is a JSONB mapping
variant name -> traffic share (equal split when NULL). Both are stored as
JSONB objects so lookups are name-based and order-independent after the
PostgreSQL jsonb key sort.
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ABExperiment(Base):
    """A/B experiment definition (sticky distribution, sequential testing)."""

    __tablename__ = "ab_experiments"
    __table_args__ = (
        Index("ix_ab_experiments_status_idx", "status"),
        CheckConstraint(
            "status IN ('active', 'stopped')",
            name="ck_ab_experiments_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    variants: Mapped[dict] = mapped_column(JSONB, nullable=False)
    weights: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    winning_variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
