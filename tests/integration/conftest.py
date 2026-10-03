"""Shared fixtures for integration tests (testcontainers PG + Redis).

Schema is created with ``Base.metadata.create_all`` (the ORM models mirror
the Alembic migrations validated in the unit suite) and the single MVP
embedding model ``bge-m3-v1`` is seeded so ``POST /index`` can resolve the
active default (ARCHITECT §5.4).
"""

from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.db.models import Base, EmbeddingModel
from app.db.queries.embedding_models import clear_default_model_cache
from app.db.redis_client import get_redis_client
from app.db.session import get_session


def _to_asyncpg_url(url: str) -> str:
    parts = urlsplit(url)
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    return f"postgresql+asyncpg://{parts.username}:{parts.password}@{host}{parts.path}"


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """Disposable PostgreSQL 16 (same image as docker-compose)."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield _to_asyncpg_url(pg.get_connection_url())


@pytest.fixture(scope="session")
def redis_address() -> Iterator[tuple[str, int]]:
    """Disposable Redis 7 (same major version as docker-compose)."""
    with RedisContainer("redis:7-alpine") as redis_c:
        host = redis_c.get_container_host_ip()
        yield host, int(redis_c.get_exposed_port(6379))


async def _seed_embedding_model(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(EmbeddingModel).values(
                name="bge-m3-v1",
                dimension=1024,
                description="BAAI/bge-m3 multilingual dense embeddings",
                is_default=True,
                is_active=True,
            )
        )
    clear_default_model_cache()


@pytest.fixture
async def engine(pg_dsn: str) -> AsyncIterator[AsyncEngine]:
    """Function-scoped async engine with a fresh schema per test."""
    engine = create_async_engine(pg_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    await _seed_embedding_model(engine)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    clear_default_model_cache()
    await engine.dispose()


@pytest.fixture
async def async_redis(redis_address: tuple[str, int]) -> AsyncIterator[aioredis.Redis]:
    """Async Redis client bound to the disposable container."""
    host, port = redis_address
    client = aioredis.Redis(host=host, port=port, decode_responses=False)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def wired_app(engine: AsyncEngine, async_redis: aioredis.Redis):
    """FastAPI app wired to the test PG + Redis via dependency overrides.

    Returns ``(app, client)`` so individual tests can re-override
    ``get_redis_client`` (e.g. to simulate an unavailable Redis).
    """
    from app.main import create_app

    wrapper = create_app()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    wrapper.dependency_overrides[get_session] = _get_session
    wrapper.dependency_overrides[get_redis_client] = lambda: async_redis

    # Override get_throttle_service to use test infrastructure
    from app.api.routes import index as index_routes
    from app.config import settings
    from app.services.throttle import OutboxThrottle

    def _get_test_throttle() -> OutboxThrottle:
        return OutboxThrottle(
            session_factory=factory,
            redis_client=async_redis,
            config=settings.throttle,
        )

    wrapper.dependency_overrides[index_routes.get_throttle_service] = _get_test_throttle

    transport = ASGITransport(app=wrapper)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield wrapper, client
    wrapper.dependency_overrides.clear()
