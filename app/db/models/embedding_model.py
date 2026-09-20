"""SQLAlchemy 2.x model for the ``embedding_models`` table (ARCHITECT §3.1, P-03)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class EmbeddingModel(Base):
    """Registry of available embedding models (one active default at a time)."""

    __tablename__ = "embedding_models"
    __table_args__ = (
        Index(
            "embedding_models_one_default_idx",
            "is_default",
            postgresql_where=text("is_default = true AND is_active = true"),
            unique=True,
        ),
    )

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    dimension: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
