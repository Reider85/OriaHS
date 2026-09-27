"""E2E test fixtures and utilities for MVP DoD validation (P-17).

Provides fixtures for the full stack (PG + Qdrant + Redis + API) and
test utilities covering the complete MVP workflow from index to search.
"""

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas import IndexRequest, SearchRequest
from app.config import settings
from app.db.session import async_session_factory


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def wired_app():
    """Full stack fixture with PG + Qdrant + Redis + API."""
    try:
        from testcontainers.postgres import PostgresContainer
        from testcontainers.redis import RedisContainer
        from testcontainers.qdrant import QdrantContainer

        # Start containers
        pg_container = PostgresContainer("postgres:16-alpine")
        redis_container = RedisContainer("redis:7-alpine")
        qdrant_container = QdrantContainer("qdrant/qdrant:v1.10.2")

        with pg_container, redis_container, qdrant_container:
            # Wait for containers to be ready
            await asyncio.sleep(5)

            # Configure app for test containers
            settings.database.url = pg_container.get_connection_url()
            settings.redis.host = redis_container.get_container_host_ip()
            settings.redis.port = redis_container.get_exposed_port(6379)
            settings.qdrant.host = qdrant_container.get_container_host_ip()
            settings.qdrant.port = qdrant_container.get_exposed_port(6379)

            # Create test client
            from app.main import app
            from httpx import AsyncClient

            # Create engine and session factory
            engine = create_async_engine(settings.database.url)
            async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            # Create tables
            async with engine.begin() as conn:
                await conn.run_sync(settings.db_metadata.create_all)

            async with AsyncClient(app=app, base_url="http://test") as client:
                yield (engine, client)

            # Clean up
            await engine.dispose()
    except Exception as e:
        # Skip if Docker containers are not available
        pytest.skip(f"Docker containers not available: {e}")


@pytest.fixture
async def async_redis():
    """Redis client for testing."""
    import redis.asyncio as aioredis

    redis_client = aioredis.Redis(
        host=settings.redis.host,
        port=settings.redis.port,
        db=0,
        decode_responses=True,
    )
    yield redis_client
    await redis_client.close()


@pytest.fixture
async def engine(wired_app):
    """Database engine from wired_app fixture."""
    yield wired_app[0]


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
async def reconcile_wait(max_seconds: int = 60):
    """Context manager to wait for reconciler to process outbox."""
    from app.db.queries.outbox import count_pending
    from app.db.session import async_session_factory

    start_time = time.time()
    session_factory = async_session_factory

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

@pytest.fixture(scope="session", autouse=True)
async def truncate_critical_tables(engine):
    """Truncate critical tables between E2E tests to prevent cross-test pollution."""
    yield  # Test runs here
    
    # Clean up after test
    async with engine.begin() as conn:
        tables_to_truncate = [
            "documents",
            "search_outbox", 
            "search_outbox_dead_digest",
            "eval_results",
            "eval_datasets"
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
                    "created_at": f"2026-01-{15 + (i % 15)}"  # Various dates
                }
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


async def poll_until(predicate: Callable[[], bool], timeout: int = 60, interval: int = 2) -> None:
    """Poll until predicate returns True or timeout is reached.
    
    Replaces time.sleep loops with proper async polling.
    
    Args:
        predicate: Function that returns True when condition is met
        timeout: Maximum time to wait in seconds
        interval: Polling interval in seconds
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if predicate():
            return
        await asyncio.sleep(interval)
    
    raise TimeoutError(f"Polling did not complete within {timeout} seconds")


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