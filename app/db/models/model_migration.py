"""Model migration ORM model for embedding model migration framework (PR-07).

Tracks migration lifecycle between embedding models with blue-green deployment,
dual-write, rollback, and snapshot capabilities.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Integer, Text, func


class MigrationStatus(str, enum.Enum):
    """Migration status enumeration."""

    INIT = "init"
    CANARY = "canary"
    CANARY_FAILED = "canary_failed"
    BACKFILL = "backfill"
    BACKFILL_FAILED = "backfill_failed"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    CLEANED = "cleaned"
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base


class ModelMigration(Base):
    """Migration record between embedding models."""

    __tablename__ = "model_migrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid4()
    )
    old_model_name: Mapped[str] = mapped_column(Text, nullable=False)
    new_model_name: Mapped[str] = mapped_column(Text, nullable=False)
    old_collection: Mapped[str] = mapped_column(Text, nullable=False)
    new_collection: Mapped[str] = mapped_column(Text, nullable=False)
    new_hf_model: Mapped[str] = mapped_column(Text, nullable=False)  # HF model ID for new model
    status: Mapped[str] = mapped_column(Text, nullable=False, default="init")

    # Canary similarity results
    canary_avg_similarity: Mapped[float | None] = mapped_column(Integer, nullable=True)
    canary_min_similarity: Mapped[float | None] = mapped_column(Integer, nullable=True)
    canary_max_similarity: Mapped[float | None] = mapped_column(Integer, nullable=True)
    canary_passed: Mapped[bool | None] = mapped_column(nullable=True)

    # S3 snapshot key for rollback
    snapshot_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Backfill progress tracking
    backfill_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backfill_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        server_default="now()",
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default="now()",
        nullable=False
    )
    promoted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cleaned_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    old_model: Mapped["EmbeddingModel"] = relationship(
        "EmbeddingModel",
        foreign_keys=[old_model_name],
        primaryjoin="and_(ModelMigration.old_model_name == EmbeddingModel.name)",
        viewonly=True,
    )
    new_model: Mapped["EmbeddingModel"] = relationship(
        "EmbeddingModel",
        foreign_keys=[new_model_name],
        primaryjoin="and_(ModelMigration.new_model_name == EmbeddingModel.name)",
        viewonly=True,
    )
