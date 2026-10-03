"""Evaluation result model (C-07)."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class EvalResult(Base):
    """Performance metrics for an evaluation run (C-07)."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(
        Text(), nullable=False
    )  # 'rrf', 'weighted', 'weighted+rerank'
    recall_at_10: Mapped[float] = mapped_column(Float(), nullable=False)
    ndcg_at_10: Mapped[float] = mapped_column(Float(), nullable=False)
    mrr: Mapped[float] = mapped_column(Float(), nullable=False)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    git_sha: Mapped[str | None] = mapped_column(Text(), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, server_default="{}")

    def __repr__(self) -> str:
        return (
            f"EvalResult(id={self.id}, strategy='{self.strategy}', recall={self.recall_at_10:.3f}, "
            f"ndcg={self.ndcg_at_10:.3f}, mrr={self.mrr:.3f}, run_at={self.run_at})"
        )
