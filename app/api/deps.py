"""FastAPI dependency helpers. P-12 will enrich these."""

from collections.abc import Iterator

from app.config import AppConfig, get_settings
from app.db.session import get_session
from app.reranker.service import RerankerService

__all__ = ["get_settings", "get_session"]


def get_app_config() -> Iterator[AppConfig]:
    """Yield the cached settings instance."""
    yield get_settings()


def get_reranker_service() -> RerankerService:
    """Get cached RerankerService instance (singleton)."""
    return RerankerService(config=get_settings().reranker)
