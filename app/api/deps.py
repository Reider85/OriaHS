"""FastAPI dependency helpers (P-12, C-01, C-03, C-04).

The reranker singletons are memoized with ``lru_cache`` on purpose: a fresh
``RerankerService`` would re-run lazy model loading on every request, and the
circuit breaker must keep one rolling window across requests for its
thresholds to mean anything.
"""

from collections.abc import Iterator
from functools import lru_cache

from app.config import AppConfig, get_settings
from app.db.session import get_session
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.service import RerankerService
from app.search.speculative import SpeculativeReranker

__all__ = [
    "get_app_config",
    "get_circuit_breaker",
    "get_reranker_service",
    "get_session",
    "get_settings",
    "get_speculative_reranker",
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
