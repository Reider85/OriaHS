"""Shared DB fixtures for model unit tests.

Uses a disposable Postgres testcontainer (dev dependency) so tests never
touch the dev database. The async engine is function-scoped so it lives in
the same event loop as the test (pytest-asyncio default). The partitioned
``search_events`` table is migration-only and not needed by ORM tests.
"""

from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from app.db.models import Base


def _to_asyncpg_url(url: str) -> str:
    parts = urlsplit(url)
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    return f"postgresql+asyncpg://{parts.username}:{parts.password}@{host}{parts.path}"


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield _to_asyncpg_url(pg.get_connection_url())


@pytest.fixture
async def engine(pg_dsn: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
