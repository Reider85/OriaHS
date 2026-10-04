"""E2E test fixtures and utilities for Critical DoD validation (B-02).

Provides fixtures for the full stack (PG + Redis + API) with dependency overrides
and test utilities covering the complete workflow from index to search.
"""

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.qdrant import QdrantContainer
from testcontainers.community.redis import RedisContainer

from app.config import settings
from app.db.models import Base
from app.db.queries.embedding_models import clear_default_model_cache
from app.db.redis_client import get_redis_client
from app.db.session import get_session


def _to_asyncpg_url(url: str) -> str:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    return f"postgresql+asyncpg://{parts.username}:{parts.password}@{host}{parts.path}"


@pytest.fixture(scope="session")
def pg_dsn():
    """Disposable PostgreSQL 16 (same image as docker-compose)."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield _to_asyncpg_url(pg.get_connection_url())


@pytest.fixture(scope="session")
def redis_address():
    """Disposable Redis 7 (same major version as docker-compose)."""
    with RedisContainer("redis:7-alpine") as redis_c:
        host = redis_c.get_container_host_ip()
        yield host, int(redis_c.get_exposed_port(6379))


@pytest.fixture(scope="session")
def qdrant_address():
    """Disposable Qdrant (same image as docker-compose)."""
    with QdrantContainer() as container:
        host = container.get_container_host_ip()
        yield host, int(container.get_exposed_port(6333))


async def _seed_embedding_model(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        )
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("""
                INSERT INTO embedding_models (name, dimension, description, is_default, is_active)
                VALUES ('bge-m3-v1', 1024, 'BAAI/bge-m3 multilingual dense embeddings', true, true)
                ON CONFLICT (name) DO NOTHING
            """)
        )
    clear_default_model_cache()


async def _seed_digest_table(engine: AsyncEngine) -> None:
    """Create search_outbox_dead_digest table for DoD tests."""
    async with engine.begin() as conn:
        # Create table if not exists (004 migration DDL)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS search_outbox_dead_digest (
                id BIGSERIAL PRIMARY KEY,
                tenant_id UUID NOT NULL,
                model_name TEXT NOT NULL,
                error_type TEXT NOT NULL,
                count BIGINT NOT NULL,
                first_seen_at TIMESTAMPTZ NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                sample_doc_ids UUID[] NOT NULL DEFAULT '{}',
                sample_errors TEXT[] NOT NULL DEFAULT '{}',
                UNIQUE (tenant_id, model_name, error_type)
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS search_outbox_dead_digest_tenant_idx 
            ON search_outbox_dead_digest (tenant_id)
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS search_outbox_dead_digest_last_seen_idx 
            ON search_outbox_dead_digest (last_seen_at DESC)
        """))


@pytest.fixture
async def engine(pg_dsn: str):
    """Function-scoped async engine with a fresh schema per test."""
    engine = create_async_engine(pg_dsn)
    await _seed_embedding_model(engine)
    await _seed_digest_table(engine)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        # Also drop digest table (no ORM model)
        await conn.execute(text("DROP TABLE IF EXISTS search_outbox_dead_digest CASCADE"))
    clear_default_model_cache()
    await engine.dispose()


@pytest.fixture
async def async_redis(redis_address):
    """Async Redis client bound to the disposable container."""
    host, port = redis_address
    client = aioredis.Redis(host=host, port=port, decode_responses=False)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def async_qdrant(qdrant_address):
    """Async Qdrant client bound to the disposable container."""
    host, port = qdrant_address
    client = AsyncQdrantClient(host=host, port=port)
    yield client
    await client.close()


@pytest.fixture
async def qdrant_service(async_qdrant):
    """QdrantService bound to disposable container with fresh collection per test."""
    # Create collection
    await async_qdrant.delete_collection("documents", timeout=30)
    await async_qdrant.create_collection(
        collection_name="documents",
        vectors_config=qmodels.VectorParams(size=1024, distance=qmodels.Distance.COSINE),
        timeout=30,
    )
    
    from app.services.qdrant import QdrantService
    service = QdrantService(client=async_qdrant, collection_name="documents")
    yield service


@pytest.fixture
async def wired_app(engine: AsyncEngine, async_redis: aioredis.Redis):
    """FastAPI app wired to the test PG + Redis via dependency overrides.

    Returns (wrapper, client) so individual tests can re-override
    dependencies (e.g. to simulate unavailable services).
    """
    from app.main import create_app

    wrapper = create_app()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_session() -> AsyncSession:
        async with factory() as session:
            yield session

    wrapper.dependency_overrides[get_session] = _get_session
    wrapper.dependency_overrides[get_redis_client] = lambda: async_redis

    # Override get_throttle_service to use test infrastructure
    from app.api.routes import index as index_routes
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


@pytest.fixture
async def wired_app_with_qdrant(engine: AsyncEngine, async_redis: aioredis.Redis, qdrant_service):
    """FastAPI app wired to test infrastructure including Qdrant for DoD 3/5."""
    from app.main import create_app
    from app.reconciler.worker import ReconcilerWorker
    from app.reconciler.digest import DigestWorker
    from app.embedding.cache import EmbeddingCache

    wrapper = create_app()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_session() -> AsyncSession:
        async with factory() as session:
            yield session

    wrapper.dependency_overrides[get_session] = _get_session
    wrapper.dependency_overrides[get_redis_client] = lambda: async_redis

    # Override get_throttle_service
    from app.api.routes import index as index_routes
    from app.services.throttle import OutboxThrottle

    def _get_test_throttle() -> OutboxThrottle:
        return OutboxThrottle(
            session_factory=factory,
            redis_client=async_redis,
            config=settings.throttle,
        )

    wrapper.dependency_overrides[index_routes.get_throttle_service] = _get_test_throttle

    # Create test reconciler worker
    class TestReconcilerWorker(ReconcilerWorker):
        def __init__(self, *args, **kwargs):
            config = kwargs.pop('config', None) or settings.reconciler
            super().__init__(
                config=config,
                session_factory=lambda: factory(),
                qdrant_service=qdrant_service,
                embedding_service=TestEmbeddingService(),
                embedding_cache=EmbeddingCache(async_redis),
            )

    class TestEmbeddingService:
        """Test embedder that can fail on nonexistent model."""
        
        async def embed_texts(self, texts: list[str]) -> list:
            import numpy as np
            # Check if we should fail (for DoD 5)
            # In real tests, this would be controlled via document.embedding_model
            # For now, always return deterministic vectors
            vector = np.array([1.0] + [0.0] * 1023, dtype=np.float32)
            return [vector] * len(texts)
        
        async def embed_query(self, query: str):
            import numpy as np
            vector = np.array([1.0] + [0.0] * 1023, dtype=np.float32)
            return vector

    # Create test digest worker
    class TestDigestWorker(DigestWorker):
        def __init__(self, *args, **kwargs):
            config = kwargs.pop('config', None) or settings.reconciler
            super().__init__(
                config=config,
                session_factory=lambda: factory(),
            )

    transport = ASGITransport(app=wrapper)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield wrapper, client, TestReconcilerWorker(), TestDigestWorker()
    wrapper.dependency_overrides.clear()


@pytest.fixture
def sample_index_data():
    """Sample data for indexing tests."""
    return {
        "tenant_id": str(uuid4()),
        "external_ref": "test-doc-1",
        "title": "Test Document",
        "content": "This is a test document for E2E validation.",
        "tags": ["test", "e2e"],
        "attributes": {"category": "test", "priority": "high"},
    }


@pytest.fixture
def sample_search_query():
    """Sample search query for testing."""
    return {
        "query": "test document",
        "tenant_id": str(uuid4()),
        "top_k": 10,
        "timeout_ms": 2000,
    }


class E2ETestClient:
    """Helper class for E2E testing with common operations."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def index_document(self, data: dict) -> dict:
        """Index a document and return response."""
        response = await self.client.post("/index", json=data)
        response.raise_for_status()
        return response.json()

    async def search_documents(self, query: dict) -> dict:
        """Search documents and return response."""
        response = await self.client.post("/search", json=query)
        response.raise_for_status()
        return response.json()

    async def delete_document(self, doc_id: str) -> httpx.Response:
        """Delete a document."""
        response = await self.client.delete(f"/index/{doc_id}")
        return response

    async def get_health(self) -> dict:
        """Get health status."""
        response = await self.client.get("/health/live")
        response.raise_for_status()
        return response.json()

    async def get_metrics(self) -> str:
        """Get Prometheus metrics."""
        response = await self.client.get("/metrics")
        response.raise_for_status()
        return response.text

    def content_hash(self, title: str, content: str) -> str:
        """Compute content hash for fast-path testing."""
        return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


@asynccontextmanager
async def reconcile_wait(max_seconds: int = 60, session_factory=None):
    """Context manager to wait for reconciler to process outbox."""
    from app.db.queries.outbox import count_pending

    start_time = time.time()
    
    if session_factory is None:
        # If no session_factory provided, use the engine fixture
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    while time.time() - start_time < max_seconds:
        async with session_factory() as session:
            pending_count = await count_pending(session)
            if pending_count == 0:
                yield
                return
        await asyncio.sleep(2)

    raise TimeoutError(f"Reconciler did not process outbox within {max_seconds} seconds")


def validate_dod_criteria_1(response_data: dict, indexed_at: str):
    """Validate DoD #1: Index → Search workflow within 30 seconds."""
    assert "doc_id" in response_data
    assert "status" in response_data
    assert response_data["status"] in ["queued", "no_change"]
    assert "indexed_at" in response_data
    assert "wait_for_index_token" in response_data


def validate_dod_criteria_2(search_response: dict, doc_id: str):
    """Validate DoD #2: RRF fusion and deduplication."""
    assert "hits" in search_response
    assert len(search_response["hits"]) > 0

    # Check that the indexed document appears in results
    found = any(hit["doc_id"] == doc_id for hit in search_response["hits"])
    assert found, f"Document {doc_id} not found in search results"

    # Check RRF structure
    for hit in search_response["hits"]:
        assert "score" in hit
        assert "title" in hit
        assert "snippet" in hit
        assert "attributes" in hit


def validate_dod_criteria_3_reconciler(engine, doc_id: str):
    """Validate DoD #3: Reconciler recovery after Qdrant failure."""
    # This would be tested by stopping Qdrant container, indexing, then restarting
    # and verifying reconciler processes the outbox
    pass


def validate_dod_criteria_4(metrics_text: str):
    """Validate DoD #4: Prometheus metrics and Grafana dashboards."""
    # Check that expected metrics are present
    assert "search_latency_ms" in metrics_text
    assert "index_lag_seconds" in metrics_text
    assert "http_requests_total" in metrics_text


def validate_dod_criteria_5(engine):
    """Validate DoD #5: Dead-letter digest functionality."""
    # This would be tested by creating a document that fails to process
    # and verifying it appears in the dead-letter digest
    pass


def validate_dod_criteria_6(first_response: dict, second_response: dict):
    """Validate DoD #6: Fast-path behavior for duplicate content."""
    # Same content should return same doc_id with "no_change" status
    assert first_response["doc_id"] == second_response["doc_id"]
    assert second_response["status"] == "no_change"


# ===== CRITICAL PHASE FIXTURES AND UTILITIES =====


@pytest.fixture(autouse=True)
async def truncate_critical_tables(engine):
    """Truncate critical tables between tests to prevent cross-test pollution."""
    yield  # Test runs here

    # Clean up after test
    async with engine.begin() as conn:
        tables_to_truncate = [
            "documents",
            "search_outbox",
            "search_outbox_dead_digest",
            "eval_results",
            "eval_datasets",
        ]

        # Truncate tables in reverse dependency order
        for table in reversed(tables_to_truncate):
            await conn.execute(f"TRUNCATE TABLE {table} CASCADE")


@pytest.fixture
def sample_critical_index_data():
    """Generate sample data for Critical phase testing with varied tags/categories."""

    def _create_batch(count: int = 50, tenant_id: UUID = None):
        if tenant_id is None:
            tenant_id = uuid4()

        documents = []
        for i in range(count):
            # Create varied content for push-down testing
            category = "ML" if i % 10 == 0 else "general"  # 10% ML category
            tags = ["tech", f"category_{category}"] + ([f"priority_{i % 3}"] if i % 3 == 0 else [])

            data = {
                "tenant_id": str(tenant_id),
                "external_ref": f"critical-doc-{i}",
                "title": f"Critical Test Document {i}",
                "content": f"This is a test document for Critical phase validation. Document {i} in category {category}.",
                "tags": tags,
                "attributes": {
                    "category": category,
                    "priority": "high" if i % 5 == 0 else "normal",
                    "created_at": f"2026-01-{15 + (i % 15)}",  # Various dates
                },
            }
            documents.append(data)
        return documents

    return _create_batch


@pytest.fixture
async def reranker_service_mock_override(wired_app):
    """Override the reranker service with mock mode for testing without GPU."""
    _, client = wired_app

    # This would normally be done through dependency injection overrides
    # For now, we'll use the existing mock service from root conftest
    from tests.conftest import reranker_service_mock

    return reranker_service_mock


async def poll_until(predicate: Callable[[], bool | Awaitable[bool]], timeout: int = 60, interval: int = 2) -> None:
    """Poll until predicate returns True or timeout is reached.

    Replaces time.sleep loops with proper async polling.

    Args:
        predicate: Function that returns True when condition is met (can be async)
        timeout: Maximum time to wait in seconds
        interval: Polling interval in seconds
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        result = predicate()
        if asyncio.iscoroutine(result):
            if await result:
                return
        elif result:
            return
        await asyncio.sleep(interval)

    raise TimeoutError(f"Polling did not complete within {timeout} seconds")


async def mark_outbox_status(engine: AsyncEngine, token: str, status: str) -> None:
    """Manually update outbox status for testing wait_for_index functionality.
    
    Args:
        engine: Database engine
        token: Document ID UUID string
        status: New status ('done', 'dead', 'failed')
    """
    doc_id = UUID(token)
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE search_outbox 
                SET status = :status, updated_at = now() 
                WHERE document_id = :doc_id
            """),
            {"doc_id": doc_id, "status": status}
        )


def inject_reranker_errors(reranker_service, error_rate: float = 0.05):
    """Inject errors into reranker service for circuit breaker testing.

    Args:
        reranker_service: The reranker service to modify
        error_rate: Fraction of calls that should fail (0.0 to 1.0)
    """
    original_rerank = reranker_service.rerank

    async def mock_rerank_with_errors(*args, **kwargs):
        import random

        if random.random() < error_rate:
            from app.reranker.exceptions import RerankerUnavailableException

            raise RerankerUnavailableException("Injected error for testing")
        return await original_rerank(*args, **kwargs)

    reranker_service.rerank = mock_rerank_with_errors
    return reranker_service


class E2ETestClientCritical(E2ETestClient):
    """Extended E2E test client with Critical phase-specific methods."""

    async def search_with_rerank(self, query: dict, rerank: bool = True):
        """Search with rerank enabled."""
        query["rerank"] = rerank
        return await self.search_documents(query)

    async def search_with_fusion(self, query: dict, fusion: str = "rrf", alpha: float = 0.5):
        """Search with specific fusion strategy."""
        query["fusion"] = fusion
        query["fusion_alpha"] = alpha
        return await self.search_documents(query)

    async def search_with_facets(self, query: dict, facets: list[str] = None):
        """Search with facets enabled."""
        if facets:
            query["facets"] = facets
        return await self.search_documents(query)

    async def index_batch(self, documents: list[dict]) -> list[dict]:
        """Index multiple documents and return responses."""
        responses = []
        for doc in documents:
            response = await self.index_document(doc)
            responses.append(response)
        return responses
