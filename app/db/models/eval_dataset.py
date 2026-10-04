"""Evaluation dataset model (C-07)."""

from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class EvalDataset(Base):
    """Metadata about an evaluation dataset (C-07)."""

    __tablename__ = "eval_datasets"

    id: Mapped[PythonUUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    version: Mapped[str] = mapped_column(Text(), nullable=False)
    path: Mapped[str] = mapped_column(Text(), nullable=False)
    query_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Unique constraint on (name, version) to avoid duplicate registrations
        UniqueConstraint("name", "version", name="uq_eval_datasets_name_version"),
    )

    def __repr__(self) -> str:
        return f"EvalDataset(id={self.id}, name='{self.name}', version='{self.version}', queries={self.query_count})"
