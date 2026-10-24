"""Chaos engineering tests for OriaHS hybrid search backend.

Tests infrastructure resilience by simulating failures:
- Qdrant container kill
- Reranker delay injection
- Redis cache saturation
- OpenTelemetry trace validation

Uses pytest markers and dependency injection for safe, isolated testing.
"""

import json
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import redis.asyncio as redis
from app.search.service import SearchService
from fastapi import FastAPI
from httpx import AsyncClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ChaosConfig, get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def chaos_config() -> ChaosConfig:
    """Chaos test configuration (ensure non-production)."""
    settings = get_settings()
    config = settings.chaos
    config.ensure_non_production()
    return config


@pytest.fixture(scope="session")
def app() -> FastAPI:
    """Create test FastAPI app."""
    return create_app()


@pytest.fixture(scope="session")
async def client(app: FastAPI) -> AsyncClient:
    """Async HTTP test client."""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


@pytest.fixture(scope="session")
async def redis_client(chaos_config: ChaosConfig) -> redis.Redis:
    """Redis client for chaos tests."""
    client = redis.Redis(
        host=chaos_config.redis_host,
        port=chaos_config.redis_port,
        decode_responses=True,
    )
    await client.flushall()  # Clean start
    return client


@pytest.fixture(scope="session")
async def qdrant_client(chaos_config: ChaosConfig) -> QdrantClient:
    """Qdrant client for chaos tests."""
    return QdrantClient(
        host=chaos_config.qdrant_host,
        port=chaos_config.qdrant_port,
    )


@pytest.fixture
async def session() -> AsyncSession:
    """Test database session."""
    from app.db.session import async_session_factory
    async with async_session_factory() as session:
        yield session


@pytest.fixture
def search_service(session: AsyncSession, chaos_config: ChaosConfig) -> SearchService:
    """Search service with chaos config."""
    return SearchService(session=session, config=chaos_config)


@pytest.fixture
def mock_query() -> dict[str, Any]:
    """Standard test query."""
    return {
        "query": "test chaos engineering",
        "tenant_id": "chaos-test-tenant",
        "filters": {},
        "rerank": True,
        "explain": True,
        "k": 10,
    }


@pytest.fixture
def metrics_report_dir(tmp_path: Path) -> Path:
    """Temporary directory for metrics reports."""
    return tmp_path / "chaos_metrics"


@pytest.fixture
def mock_span_processor(metrics_report_dir: Path):
    """Mock span processor that saves traces to files."""
    processor = SimpleSpanProcessor(
        lambda span: metrics_report_dir.joinpath(f"trace_{span.span_id}.json").write_text(
            json.dumps(
                {
                    "trace_id": span.context.trace_id,
                    "span_id": span.span_id,
                    "name": span.name,
                    "kind": span._kind.name if span._kind else None,
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "status": span.status.status_code if span.status else None,
                    "attributes": dict(span.attributes),
                    "events": [
                        {"name": e.name, "timestamp": e.timestamp, "attributes": dict(e.attributes)}
                        for e in span.events
                    ],
                }
            )
        )
    )
    return processor


@pytest.fixture(autouse=True)
def setup_tracing_for_chaos(
    chaos_config: ChaosConfig,
    mock_span_processor: SimpleSpanProcessor
) -> Generator[None, None, None]:
    """Setup tracing for chaos tests with mock processor."""
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(mock_span_processor)

    # Override global tracer setup
    from app.observability.tracing import tracer
    original_provider = tracer.provider
    tracer.provider = tracer_provider

    try:
        yield
    finally:
        tracer.provider = original_provider


@pytest.fixture
def chaos_metrics(metrics_report_dir: Path):
    """Chaos-specific metrics collector."""
    metrics = {}

    def record_metric(name: str, value: float, labels: dict[str, str] = None):
        labels = labels or {}
        key = f"{name}_{json.dumps(labels, sort_keys=True)}"
        metrics[key] = value

    def get_metric(name: str, labels: dict[str, str] = None) -> float:
        labels = labels or {}
        key = f"{name}_{json.dumps(labels, sort_keys=True)}"
        return metrics.get(key, 0.0)

    return record_metric, get_metric


@pytest.fixture
def search_slo_timer(chaos_config: ChaosConfig):
    """Timer for search SLO validation."""
    start_time = time.monotonic()

    def check_slo():
        elapsed = (time.monotonic() - start_time) * 1000  # ms
        return elapsed <= chaos_config.search_slo_ms

    return check_slo, start_time


@pytest.fixture
async def sample_documents(session: AsyncSession):
    """Add sample documents for testing."""
    from app.models import Document

    documents = [
        Document(
            tenant_id="chaos-test-tenant",
            title="Chaos Engineering Overview",
            content="Chaos engineering is the discipline of experimenting on systems...",
            metadata={"category": "engineering"},
        ),
        Document(
            tenant_id="chaos-test-tenant",
            title="Resilience Testing",
            content="Building resilient systems through controlled failures...",
            metadata={"category": "testing"},
        ),
    ]

    session.add_all(documents)
    await session.commit()
    return documents


# Chaos-specific fixtures for each failure mode
@pytest.fixture
def qdrant_kill_command(chaos_config: ChaosConfig) -> str:
    """Docker command to kill Qdrant container."""
    return f"docker kill {chaos_config.qdrant_container_name}"


@pytest.fixture
def reranker_delay_config(chaos_config: ChaosConfig) -> dict[str, int]:
    """Reranker delay configuration for testing."""
    return {
        "mock_delay_ms": chaos_config.reranker_config.mock_delay_ms,
        "timeout_ms": chaos_config.reranker_config.timeout_ms,
    }


@pytest.fixture
def redis_fill_command(chaos_config: ChaosConfig) -> str:
    """Command to fill Redis cache."""
    return f"docker exec {chaos_config.qdrant_container_name} redis-cli --eval /tmp/fill_redis.lua 0 {chaos_config.redis_maxmemory_mb}"


@pytest.fixture
def trace_validation_config(chaos_config: ChaosConfig) -> dict[str, Any]:
    """Configuration for trace validation."""
    return {
        "expected_services": ["search", "qdrant", "reranker"],
        "max_span_duration_ms": chaos_config.search_slo_ms,
        "required_attributes": ["tenant_id", "request_id"],
    }
