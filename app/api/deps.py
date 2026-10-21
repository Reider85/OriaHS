"""FastAPI dependency helpers (P-12, C-01, C-03, C-04).

The reranker singletons are memoized with ``lru_cache`` on purpose: a fresh
``RerankerService`` would re-run lazy model loading on every request, and the
circuit breaker must keep one rolling window across requests for its
thresholds to mean anything.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AppConfig, get_settings
from app.db.session import get_session
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.service import RerankerService
from app.search.speculative import SpeculativeReranker

if TYPE_CHECKING:
    from app.services.ab_testing import ABTestingService
    from app.services.personalization import PersonalizationService

__all__ = [
    "get_ab_testing_service",
    "get_app_config",
    "get_circuit_breaker",
    "get_reranker_service",
    "get_session",
    "get_settings",
    "get_speculative_reranker",
    "get_personalization_service",
]


def get_app_config() -> Iterator[AppConfig]:
    """Yield the cached settings instance."""
    yield get_settings()


@lru_cache
def get_reranker_service() -> RerankerService:
    """Get cached RerankerService instance (singleton).

    The cross-encoder is loaded once and reused; C-01 requires this to be a
    singleton so ``FlagReranker`` is not re-initialised per request.
    """
    return RerankerService(config=get_settings().reranker)


@lru_cache
def get_circuit_breaker() -> RerankerCircuitBreaker:
    """Get cached RerankerCircuitBreaker instance (singleton).

    The rolling window spans requests — a per-request breaker could never
    accumulate enough samples to reach the 5% error-rate threshold.
    """
    return RerankerCircuitBreaker(config=get_settings().circuit_breaker)


@lru_cache
def get_speculative_reranker() -> SpeculativeReranker:
    """Get cached SpeculativeReranker instance (singleton)."""
    return SpeculativeReranker(
        get_reranker_service(), get_circuit_breaker(), get_settings().reranker
    )


def get_personalization_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> "PersonalizationService":
    """Factory for PersonalizationService (session-bound, not cached)."""
    from app.services.personalization import PersonalizationService

    return PersonalizationService(session, get_settings().personalization)


def get_ab_testing_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> "ABTestingService":
    """Factory for ABTestingService (session-bound per request, not cached).

    Returns an *instance* so orchestrator/routes can call ``assign_variant``
    and ``get_experiment`` directly; the 60s experiment cache lives on the
    class and is shared across requests.
    """
    from app.services.ab_testing import ABTestingService

    return ABTestingService(session, get_settings().ab_testing)
