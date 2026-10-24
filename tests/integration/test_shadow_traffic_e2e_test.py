"""Integration tests for ShadowTrafficService (PR-08).

Tests end-to-end shadow traffic functionality including experiment creation,
shadow execution, and comparison metrics. Uses testcontainers for PostgreSQL
and mocks for external services.
"""

import asyncio
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.api.schemas import ShadowExperimentCreate
from app.config import AppConfig, ShadowTrafficConfig
from app.db.session import async_session_factory
from app.services.shadow_traffic import ShadowTrafficService
from app.services.ab_testing import ABTestingService


@pytest.fixture(scope="session")
def postgres_container() -> PostgresContainer:
    """Start PostgreSQL container for integration tests."""
    with PostgresContainer("postgres:15") as postgres:
        yield postgres


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    """Get PostgreSQL connection URL from container."""
    return postgres_container.get_connection_url().replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def engine(postgres_url: str) -> AsyncEngine:
    """Create async engine for tests."""
    return create_async_engine(postgres_url)


@pytest.fixture(scope="session")
async def engine_setup(engine: AsyncEngine):
    """Create database tables and test data."""
    # Import here to avoid issues with test discovery
    from app.db.base import Base
    from app.db.models.search_event import SearchEvent
    from app.db.models.user_context_profile import UserContextProfile
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
# Create test tenant
    async with async_session_factory() as session:
        from app.db.models.tenant import Tenant
        tenant = Tenant(tenant_id=uuid.uuid4(), name="test-tenant")
        session.add(tenant)
        await session.commit()

    """Create test FastAPI app with overridden dependencies."""
    from app.main import create_app
    
    app = create_app()
    
    # Override database dependency
    async def override_get_session():
        async with async_session_factory() as session:
            yield session
    
    app.dependency_overrides[async_session_factory] = override_get_session
    
    # Override app config with test settings
    test_config = AppConfig()
    test_config.feature_flags.ab_testing_enabled = True
    test_config.feature_flags.shadow_traffic_enabled = True
    test_config.shadow_traffic = ShadowTrafficConfig(
        enabled=True,
        experiment_id="shadow-test",
        strategies=["weighted+rerank"],
        sample_rate=1.0,
    )
    
    app.dependency_overrides[lambda: test_config] = lambda: test_config
    
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    """Create HTTP test client."""
    return AsyncClient(app=app, base_url="http://test")


@pytest.fixture
def mock_embedding_service():
    """Mock embedding service for testing."""
    with patch("app.services.shadow_traffic.EmbeddingService") as mock:
        mock_instance = AsyncMock()
        mock_instance.embed_query.return_value = [0.1, 0.2, 0.3]  # Mock embeddings
        mock_instance.embed_documents.return_value = [[0.1, 0.2, 0.3]]  # Mock embeddings
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_qdrant_client():
    """Mock Qdrant client for testing."""
    with patch("app.services.shadow_traffic.AsyncQdrantClient") as mock:
        mock_instance = AsyncMock()
        mock_instance.search.return_value = []  # Mock empty results
        mock.return_value = mock_instance
        yield mock_instance


class TestShadowTrafficE2E:
    """End-to-end tests for shadow traffic functionality."""

    async def test_shadow_experiment_creation(self, client: AsyncClient):
        """Test creation of shadow traffic experiment."""
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow experiment",
            control_strategy="rrf",
            treatment_strategies=["weighted+rerank", "rrf+personalize"],
        )
        
        response = await client.post("/experiments/shadow", json=payload.model_dump())
        assert response.status_code == 201
        assert response.json() == {"experiment_id": experiment_id}
        
        # Test duplicate creation
        duplicate_response = await client.post("/experiments/shadow", json=payload.model_dump())
        assert duplicate_response.status_code == 409

    async def test_shadow_experiment_creation_with_disabled_flags(self, client: AsyncClient):
        """Test shadow experiment creation when flags are disabled."""
        # Disable shadow flag
        from app.api.deps import get_app_config
        original_config = get_app_config()
        
        def disabled_config():
            config = original_config()
            config.feature_flags.shadow_traffic_enabled = False
            return config
        
        client.app.dependency_overrides[get_app_config] = disabled_config
        
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow experiment",
            control_strategy="rrf",
            treatment_strategies=["weighted+rerank"],
        )
        
        response = await client.post("/experiments/shadow", json=payload.model_dump())
        assert response.status_code == 403
        
        # Restore original config
        client.app.dependency_overrides.clear()

    async def test_shadow_experiment_creation_invalid_strategy(self, client: AsyncClient):
        """Test shadow experiment creation with invalid strategy."""
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow experiment",
            control_strategy="rrf",
            treatment_strategies=["invalid_strategy"],
        )
        
        response = await client.post("/experiments/shadow", json=payload.model_dump())
        assert response.status_code == 422
        assert "Invalid strategy" in response.json()["detail"]

    async def test_shadow_search_execution(self, client: AsyncClient, mock_embedding_service, mock_qdrant_client):
        """Test shadow traffic search execution."""
        # First create an experiment
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow experiment",
            control_strategy="rrf",
            treatment_strategies=["weighted+rerank"],
        )
        
        await client.post("/experiments/shadow", json=payload.model_dump())
        
        # Execute search with shadow traffic
        search_request = {
            "query": "test search query",
            "tenant_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "top_k": 10,
        }
        
        response = await client.post("/search", json=search_request)
        assert response.status_code == 200
        
        # Verify shadow events were logged (poll for a short time)
        from app.config import SearchEventsConfig
        from app.db.queries.search_events import SearchEventQueries
        
        # Wait up to 3 seconds for shadow events to be logged
        timeout = 3.0
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            async with async_session_factory() as session:
                result = await session.execute(
                    "SELECT COUNT(*) FROM search_events WHERE experiment_id = :exp_id",
                    {"exp_id": experiment_id}
                )
                count = result.scalar()
                
                if count >= 2:  # Control + treatment
                    break
                    
            await asyncio.sleep(0.1)
        
        # Verify we have shadow events
        async with async_session_factory() as session:
            result = await session.execute(
                "SELECT variant, COUNT(*) FROM search_events WHERE experiment_id = :exp_id GROUP BY variant",
                {"exp_id": experiment_id}
            )
            rows = result.fetchall()
            
            # Should have control and treatment variants
            variants = {row[0] for row in rows}
            assert "control" in variants
            assert "treatment" in variants

    async def test_shadow_search_without_experiment(self, client: AsyncClient):
        """Test normal search without shadow traffic."""
        search_request = {
            "query": "test search query",
            "tenant_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "top_k": 10,
        }
        
        response = await client.post("/search", json=search_request)
        assert response.status_code == 200
        
        # Verify only one search event was logged (no shadow)
        from app.config import SearchEventsConfig
        from app.db.queries.search_events import SearchEventQueries
        
        # Wait briefly for event to be logged
        await asyncio.sleep(0.5)
        
        async with async_session_factory() as session:
            result = await session.execute(
                "SELECT COUNT(*) FROM search_events WHERE query = :query",
                {"query": "test search query"}
            )
            count = result.scalar()
            assert count == 1  # Only main search, no shadow

    async def test_shadow_comparison_endpoint(self, client: AsyncClient):
        """Test shadow traffic comparison metrics endpoint."""
        # Create experiment first
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow experiment",
            control_strategy="rrf",
            treatment_strategies=["weighted+rerank"],
        )
        
        await client.post("/experiments/shadow", json=payload.model_dump())
        
        # Insert some test search events
        async with async_session_factory() as session:
            from app.config import SearchEventsConfig
            from app.db.queries.search_events import SearchEventQueries
            
            # Insert control events
            for _ in range(10):
                await queries.insert_search_event(
                    session,
                    user_id=str(uuid.uuid4()),
                    tenant_id=uuid.uuid4(),
                    query_text="test query",
                    query_lang=None,
                    top_doc_ids=[],
                    fusion_strategy="rrf",
                    experiment_id=experiment_id,
                    variant="control",
                    latency_ms=100,
                )
            
            # Insert treatment events
            for _ in range(8):
                await queries.insert_search_event(
                    session,
                    user_id=str(uuid.uuid4()),
                    tenant_id=uuid.uuid4(),
                    query_text="test query",
                    query_lang=None,
                    top_doc_ids=[],
                    fusion_strategy="weighted",
                    experiment_id=experiment_id,
                    variant="treatment",
                    latency_ms=120,
                )
            
            # Insert click events
            for _ in range(5):
                await queries.insert_search_event(
                    session,
                    user_id=str(uuid.uuid4()),
                    tenant_id=uuid.uuid4(),
                    query_text="test query",
                    clicked_doc_id=uuid.uuid4(),
                    position=1,
                    experiment_id=experiment_id,
                    variant="control",
                )
            
            for _ in range(3):
                await queries.insert_search_event(
                    session,
                    user_id=str(uuid.uuid4()),
                    tenant_id=uuid.uuid4(),
                    query_text="test query",
                    clicked_doc_id=uuid.uuid4(),
                    position=1,
                    experiment_id=experiment_id,
                    variant="treatment",
                )
        
        # Get comparison metrics
        date_from = date.today() - timedelta(days=1)
        date_to = date.today()
        
        response = await client.get(
            f"/experiments/shadow/{experiment_id}/comparison?date_from={date_from}&date_to={date_to}"
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "control" in data
        assert "treatment" in data
        
        # Verify metrics structure
        control = data["control"]
        treatment = data["treatment"]
        
        assert "count" in control
        assert "ctr" in control
        assert "mrr" in control
        assert "no_click_rate" in control
        assert "latency_p95" in control
        
        assert "count" in treatment
        assert "ctr" in treatment
        assert "mrr" in treatment
        assert "no_click_rate" in treatment
        assert "latency_p95" in treatment
        
        # Verify values make sense
        assert control["count"] == 10
        assert treatment["count"] == 8
        assert control["ctr"] == 5/10  # 5 clicks / 10 searches
        assert treatment["ctr"] == 3/8  # 3 clicks / 8 searches

    async def test_shadow_comparison_nonexistent_experiment(self, client: AsyncClient):
        """Test comparison endpoint with non-existent experiment."""
        experiment_id = "nonexistent-exp"
        date_from = date.today() - timedelta(days=1)
        date_to = date.today()
        
        response = await client.get(
            f"/experiments/shadow/{experiment_id}/comparison?date_from={date_from}&date_to={date_to}"
        )
        assert response.status_code == 404

    async def test_shadow_sampling_statistical(self, client: AsyncClient):
        """Test shadow traffic sampling with statistical distribution."""
        # Set low sample rate
        from app.api.deps import get_app_config
        original_config = get_app_config()
        
        def low_sample_config():
            config = original_config()
            config.shadow_traffic.sample_rate = 0.1
            return config
        
        client.app.dependency_overrides[get_app_config] = low_sample_config
        
        # Create experiment
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow sampling",
            control_strategy="rrf",
            treatment_strategies=["weighted+rerank"],
        )
        
        await client.post("/experiments/shadow", json=payload.model_dump())
        
        # Execute multiple searches
        search_count = 100
        shadow_count = 0
        
        for i in range(search_count):
            search_request = {
                "query": f"test query {i}",
                "tenant_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "top_k": 10,
            }
            
            response = await client.post("/search", json=search_request)
            assert response.status_code == 200
            
            # Check if shadow events were created
            async with async_session_factory() as session:
                result = await session.execute(
                    "SELECT COUNT(*) FROM search_events WHERE experiment_id = :exp_id",
                    {"exp_id": experiment_id}
                )
                if result.scalar() > 0:
                    shadow_count += 1
            
            await asyncio.sleep(0.01)  # Small delay between requests
        
        # Restore original config
        client.app.dependency_overrides.clear()
        
        # Statistical test: should be approximately 10% (±5% tolerance)
        expected_min = search_count * 0.05  # 5%
        expected_max = search_count * 0.15  # 15%
        
        assert expected_min <= shadow_count <= expected_max, \
            f"Shadow count {shadow_count} outside expected range [{expected_min}, {expected_max}]"

    async def test_feature_flag_disabled_behavior(self, client: AsyncClient):
        """Test behavior when shadow traffic feature flag is disabled."""
        # Disable shadow flag
        from app.api.deps import get_app_config
        original_config = get_app_config()
        
        def disabled_config():
            config = original_config()
            config.feature_flags.shadow_traffic_enabled = False
            return config
        
        client.app.dependency_overrides[get_app_config] = disabled_config
        
        # Try to create shadow experiment (should fail)
        experiment_id = f"shadow-{uuid.uuid4().hex[:8]}"
        payload = ShadowExperimentCreate(
            experiment_id=experiment_id,
            description="Test shadow experiment",
            control_strategy="rrf",
            treatment_strategies=["weighted+rerank"],
        )
        
        response = await client.post("/experiments/shadow", json=payload.model_dump())
        assert response.status_code == 403
        
        # Execute normal search (should work without shadow)
        search_request = {
            "query": "test search query",
            "tenant_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "top_k": 10,
        }
        
        response = await client.post("/search", json=search_request)
        assert response.status_code == 200
        
        # Verify no shadow events
        async with async_session_factory() as session:
            result = await session.execute(
                "SELECT COUNT(*) FROM search_events WHERE experiment_id IS NOT NULL"
            )
            assert result.scalar() == 0
        
        # Restore original config
        client.app.dependency_overrides.clear()