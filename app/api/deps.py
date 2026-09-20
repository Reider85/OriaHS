"""FastAPI dependency helpers. P-12 will enrich these."""

from collections.abc import Iterator

from app.config import AppConfig, get_settings
from app.db.session import get_session

__all__ = ["get_settings", "get_session"]


def get_app_config() -> Iterator[AppConfig]:
    """Yield the cached settings instance."""
    yield get_settings()
