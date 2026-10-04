"""Query helpers for the ``embedding_models`` registry (ARCHITECT §5.4, P-03)."""

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmbeddingModel

_DEFAULT_CACHE_TTL_SECONDS = 300

_default_model_cache: TTLCache[str, EmbeddingModel] = TTLCache(
    maxsize=8,
    ttl=_DEFAULT_CACHE_TTL_SECONDS,
)


def clear_default_model_cache() -> None:
    """Drop the process-level cache (used by tests to avoid cross-fixture staleness)."""
    _default_model_cache.clear()


async def get_default_model(session: AsyncSession) -> EmbeddingModel:
    """Return the single active default model, cached per-process with TTL."""
    cached = _default_model_cache.get("default")
    if cached is not None:
        return cached

    model = await session.scalar(
        select(EmbeddingModel).where(
            EmbeddingModel.is_default.is_(True),
            EmbeddingModel.is_active.is_(True),
        )
    )
    if model is None:
        raise RuntimeError("No active default embedding model registered in the database")

    _default_model_cache["default"] = model
    return model


async def get_active_model_by_name(session: AsyncSession, model_name: str) -> EmbeddingModel:
    """Return an active model by name, or raise ValueError if not found/inactive."""
    model = await session.scalar(
        select(EmbeddingModel).where(
            EmbeddingModel.name == model_name,
            EmbeddingModel.is_active.is_(True),
        )
    )
    if model is None:
        raise ValueError(f"Embedding model '{model_name}' is not active or not found")
    return model
