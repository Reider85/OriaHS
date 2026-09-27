"""Evaluation dataset and result queries (C-07)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.eval_dataset import EvalDataset
from app.db.models.eval_result import EvalResult


async def register_dataset(
    session: AsyncSession,
    name: str,
    version: str,
    path: str,
    query_count: int,
) -> EvalDataset:
    """Register or get existing dataset (upsert by name+version).
    
    Returns the existing EvalDataset if already registered, otherwise creates a new one.
    """
    # Check if dataset with same name+version already exists
    stmt = select(EvalDataset).where(
        EvalDataset.name == name, EvalDataset.version == version
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        return existing
    
    # Create new dataset
    dataset = EvalDataset(
        name=name,
        version=version,
        path=path,
        query_count=query_count,
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


async def save_result(
    session: AsyncSession,
    dataset_id: str,
    strategy: str,
    recall_at_10: float,
    ndcg_at_10: float,
    mrr: float,
    git_sha: str | None = None,
    extra: dict[str, Any] | None = None,
) -> EvalResult:
    """Save evaluation result metrics."""
    result = EvalResult(
        dataset_id=dataset_id,
        strategy=strategy,
        recall_at_10=recall_at_10,
        ndcg_at_10=ndcg_at_10,
        mrr=mrr,
        git_sha=git_sha,
        extra=extra or {},
    )
    session.add(result)
    await session.commit()
    await session.refresh(result)
    return result


async def get_baseline(
    session: AsyncSession, dataset_id: str, strategy: str
) -> EvalResult | None:
    """Get the latest result for dataset+strategy (baseline for regression detection)."""
    stmt = select(EvalResult).where(
        EvalResult.dataset_id == dataset_id,
        EvalResult.strategy == strategy,
    ).order_by(EvalResult.run_at.desc())
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_results_by_dataset(
    session: AsyncSession, dataset_id: str, limit: int = 100
) -> list[EvalResult]:
    """Get all results for a dataset, ordered by most recent first."""
    stmt = select(EvalResult).where(
        EvalResult.dataset_id == dataset_id
    ).order_by(EvalResult.run_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_latest_run_timestamp(session: AsyncSession) -> datetime | None:
    """Get the timestamp of the most recent eval run (for observability)."""
    stmt = select(func.max(EvalResult.run_at)).select_from(EvalResult)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


__all__ = [
    "register_dataset",
    "save_result", 
    "get_baseline",
    "get_results_by_dataset",
    "get_latest_run_timestamp",
]