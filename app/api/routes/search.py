"""``POST /search`` read-path route (P-11, ARCHITECT §14.1).

Orchestrates parallel lexical + vector channels with RRF fusion.
Returns top-k results with latency and degradation metadata.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchRequest, SearchResponse
from app.config import settings
from app.db.session import get_session
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.search.orchestrator import SearchOrchestrator
from app.search.qdrant_client import get_async_qdrant_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _get_embedding_service() -> EmbeddingService:
    """Lazy singleton for the embedding service."""
    return EmbeddingService(config=settings.embedding)


def _get_embedding_cache() -> EmbeddingCache:
    """Lazy singleton for the embedding cache."""
    from app.db.redis_client import get_redis_client

    return EmbeddingCache(redis_client=get_redis_client())


@router.post("", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    session: SessionDep,
) -> SearchResponse:
    """Run hybrid search: lexical + vector channels fused via RRF."""
    qdrant_client = get_async_qdrant_client()
    embedding_service = _get_embedding_service()
    embedding_cache = _get_embedding_cache()

    orchestrator = SearchOrchestrator(
        session=session,
        qdrant_client=qdrant_client,
        embedding_service=embedding_service,
        embedding_cache=embedding_cache,
        config=settings.search,
    )

    try:
        return await orchestrator.search(req)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Search timeout",
        ) from exc
    except Exception as exc:
        logger.exception("Search failed unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {exc}",
        ) from exc
