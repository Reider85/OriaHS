"""Async SQLAlchemy engine/session factory.

Engine is created lazily (on first access) so importing the app does not
open database connections. Only the async engine + asyncpg is supported.
"""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

naming_meta = MetaData(naming_convention=convention)


def create_engine() -> AsyncEngine:
    """Build the async engine from settings (call once per process)."""
    return create_async_engine(settings.postgres.dsn, echo=settings.debug)


engine = create_engine()
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a scoped async session."""
    async with async_session_factory() as session:
        yield session
