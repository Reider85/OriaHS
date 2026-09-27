"""FastAPI dependency helpers. P-12 will enrich these."""

from collections.abc import Iterator
from functools import lru_cache

from app.config import AppConfig, get_settings
from app.db.session import get_session
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.service import RerankerService
from app.search.speculative import SpeculativeReranker

__all__ = ["get_settings", "get_session"]


def get_app_config() -> Iterator[AppConfig]:
    """Yield the cached settings instance."""
    yield get_settings()


def get_reranker_service() -> RerankerService:
    """Get cached RerankerService instance (singleton)."""
    return RerankerService(config=get_settings().reranker)


@lru_cache
def get_circuit_breaker() -> RerankerCircuitBreaker:
    """Get cached RerankerCircuitBreaker instance (singleton)."""
    return RerankerCircuitBreaker(config=get_settings().circuit_breaker)


def get_speculative_reranker() -> SpeculativeReranker:
    """Get cached SpeculativeReranker instance (singleton)."""
    reranker_service = get_reranker_service()
    circuit_breaker = get_circuit_breaker()
    config = get_settings().reranker
    return SpeculativeReranker(reranker_service, circuit_breaker, config)
