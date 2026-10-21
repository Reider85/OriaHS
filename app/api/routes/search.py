"""``POST /search`` read-path route (P-11, C-05, ARCHITECT §14.1).

Orchestrates parallel lexical + vector channels, fuses them (RRF or
weighted), and optionally reranks with the cross-encoder through the
circuit breaker. Always answers ``200`` — a degraded pipeline is reported
via the ``degraded`` / ``partial`` flags, never via a 5xx.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_ab_testing_service,
    get_circuit_breaker,
    get_personalization_service,
    get_reranker_service,
    get_speculative_reranker,
)
from app.api.schemas import ClickRequest, SearchRequest, SearchResponse
from app.config import settings
from app.db.session import get_session
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.service import RerankerService
from app.search.orchestrator import SearchOrchestrator
from app.search.qdrant_client import get_async_qdrant_client
from app.search.speculative import SpeculativeReranker
from app.services.ab_testing import ABTestingService
from app.services.personalization import PersonalizationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RerankerDep = Annotated[RerankerService, Depends(get_reranker_service)]
CircuitBreakerDep = Annotated[RerankerCircuitBreaker, Depends(get_circuit_breaker)]
SpeculativeDep = Annotated[SpeculativeReranker, Depends(get_speculative_reranker)]
PersonalizationDep = Annotated["PersonalizationService", Depends(get_personalization_service)]
ABTestingDep = Annotated[ABTestingService, Depends(get_ab_testing_service)]


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
    reranker_service: RerankerDep,
    circuit_breaker: CircuitBreakerDep,
    speculative_reranker: SpeculativeDep,
    personalization_service: PersonalizationDep,
    ab_testing_service: ABTestingDep,
) -> SearchResponse:
    """Run hybrid search: lexical + vector channels, fusion, optional rerank."""
    qdrant_client = get_async_qdrant_client()
    embedding_service = _get_embedding_service()
    embedding_cache = _get_embedding_cache()

    orchestrator = SearchOrchestrator(
        session=session,
        qdrant_client=qdrant_client,
        embedding_service=embedding_service,
        embedding_cache=embedding_cache,
        config=settings.search,
        reranker=reranker_service,
        circuit_breaker=circuit_breaker,
        speculative_reranker=speculative_reranker,
        reranker_config=settings.reranker,
        feature_flags=settings.feature_flags,
        personalization_service=personalization_service,
        ab_testing_service=ab_testing_service,
        mmr_config=settings.mmr,
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


@router.post("/click", status_code=status.HTTP_200_OK)
async def log_click(
    req: ClickRequest,
    session: SessionDep,
    personalization_service: PersonalizationDep,
    ab_testing_service: ABTestingDep,
) -> dict[str, bool]:
    """Log user click interaction for analytics and personalization."""
    from app.db.queries.search_events import search_event_queries

    try:
        # Recompute the A/B variant server-side (sticky hash, stateless) so
        # the click is attributed to the same arm as the originating search.
        # Assignment metrics are not incremented here — only searches count.
        variant = None
        if (
            req.experiment_id
            and req.user_id is not None
            and settings.feature_flags.ab_testing_enabled
        ):
            experiment = await ab_testing_service.get_experiment(req.experiment_id)
            if experiment is not None:
                variant = ab_testing_service.assign_variant(
                    req.user_id,
                    req.experiment_id,
                    list(experiment.variants.keys()),
                    experiment.weights,
                )

        # Log the click event
        await search_event_queries.insert_click_event(
            session=session,
            user_id=str(req.user_id) if req.user_id else None,
            tenant_id=str(req.tenant_id),
            query_text=req.query,
            clicked_doc_id=str(req.clicked_doc_id),
            position=req.position,
            experiment_id=req.experiment_id,
            variant=variant,
        )

        # Update user profile if personalization is enabled and user_id is provided
        if settings.feature_flags.personalization_enabled and req.user_id is not None:
            # Run personalization update in background to not block response
            import asyncio

            async def update_profile():
                try:
                    # Default profile update
                    embedding_service = _get_embedding_service()
                    await personalization_service.update_on_click(
                        user_id=req.user_id,
                        tenant_id=req.tenant_id,
                        clicked_doc_id=req.clicked_doc_id,
                        embedding_service=embedding_service,
                    )

                    # Contextual profile update if context provided and flag on
                    if (
                        req.context is not None
                        and settings.feature_flags.context_personalization_enabled
                    ):
                        await personalization_service.update_on_click_contextual(
                            user_id=req.user_id,
                            tenant_id=req.tenant_id,
                            clicked_doc_id=req.clicked_doc_id,
                            embedding_service=embedding_service,
                            context=req.context,
                        )
                except Exception as e:
                    logger.warning(f"Failed to update user profile: {e}")

            asyncio.create_task(update_profile())

        return {"logged": True}

    except Exception as exc:
        logger.exception("Click logging failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Click logging failed: {exc}",
        ) from exc
