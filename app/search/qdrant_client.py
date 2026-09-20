"""Qdrant client factories (ARCHITECT §17.2, P-04).

Only the async client is used from hot paths; the sync one exists for
one-shot scripts (init_qdrant.py, reconciler-free tooling).
"""

from functools import lru_cache
from typing import Any

from qdrant_client import AsyncQdrantClient, QdrantClient

from app.config import settings


def _client_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"url": settings.qdrant.url}
    api_key = settings.qdrant.api_key
    if api_key:
        kwargs["api_key"] = api_key
    return kwargs


@lru_cache
def get_qdrant_client() -> QdrantClient:
    """Sync client (scripts, tooling)."""
    return QdrantClient(**_client_kwargs())


@lru_cache
def get_async_qdrant_client() -> AsyncQdrantClient:
    """Async client (API/reconciler hot paths)."""
    return AsyncQdrantClient(**_client_kwargs())
