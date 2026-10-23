"""Integration tests for migration framework (PR-07).

Tests end-to-end migration workflow with real database and mocked services.
Requires Docker for PostgreSQL testcontainer.
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client.http import models as qdrant_models
from testcontainers.postgres import PostgresContainer

from app.config import settings
from app.db.models.embedding_model import EmbeddingModel
from app.db.models.model_migration import MigrationStatus, ModelMigration
from app.db.queries.model_migrations import (
    get_active_search_target,
    get_dual_write_targets,
)
from app.embedding.service import EmbeddingService
from app.migration import MigrationService


@pytest.fixture(scope="session")
def postgres_container():
    """Create PostgreSQL test container."""
    with PostgresContainer("postgres:15-alpine") as postgres:
        yield postgres


@pytest.fixture(scope="session")
async def test_engine(postgres_container):
    """Create test database engine."""
    # Override database URL to use test container
    original_url = settings.database_url
    settings.database_url = f"postgresql+asyncpg://postgres:postgres@{postgres_container.get_container_host_ip()}:{postgres_container.get_exposed_port(5432)}/testdb"

    try:
        from app.db.session import async_engine
        yield async_engine
    finally:
        settings.database_url = original_url


@pytest.fixture
async def session(test_engine):
    """Create test database session."""
    from app.db.session import async_session_factory
    async with async_session_factory() as session:
        yield session


@pytest.fixture
async def qdrant_client():
    """Create mock Qdrant client."""
    client = AsyncMock()
    client.collection_exists = AsyncMock(return_value=False)
    client.create_collection = AsyncMock()
    client.upsert = AsyncMock()
    client.retrieve = AsyncMock(return_value=[])
    client.get_collection = AsyncMock()
    return client


@pytest.fixture
def embedding_service():
    """Create mock embedding service."""
    service = AsyncMock(spec=EmbeddingService)
    service.embed_texts = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
    service.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    return service


@pytest.fixture
def migration_service(session, qdrant_client, embedding_service):
    """Create MigrationService with test dependencies."""
    return MigrationService(
        session=session,
        qdrant_client=qdrant_client,
        embedding_old=embedding_service,
        embedding_new=embedding_service,
    )


@pytest.mark.asyncio
async def test_migration_workflow(session, migration_service, qdrant_client, embedding_service):
    """Test complete migration workflow."""
    # Setup: Create initial model
    initial_model = EmbeddingModel(
        id=uuid.uuid4(),
        name="bge-m3-v1",
        dimension=1024,
        is_default=True,
        is_active=True,
    )
    session.add(initial_model)
    await session.commit()

    # Step 1: Initialize migration
    migration_id = await migration_service.init_migration(
        new_model_name="bge-m3-v2",
        new_dimension=1024,
        new_hf_model="BAAI/bge-m3-v2",
    )

    assert migration_id is not None
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.status == MigrationStatus.INIT

    # Step 2: Run canary reindex
    # Mock document data
    doc_id = uuid.uuid4()
    doc = EmbeddingModel(
        id=doc_id,
        name="bge-m3-v2",
        dimension=1024,
        is_default=False,
        is_active=True,
        content="test document content for canary",
    )
    session.add(doc)
    await session.commit()

    # Mock Qdrant retrieve for old vectors
    old_vectors = [
        qdrant_models.PointStruct(
            id=str(doc_id),
            vector=[0.1, 0.2, 0.3, 0.4],
            payload={"content_hash": "abc123"},
        )
    ]
    qdrant_client.retrieve.return_value = old_vectors

    # Mock embedding service
    embedding_service.embed_texts.return_value = [[0.2, 0.3, 0.4, 0.5]]

    # Run canary
    canary_result = await migration_service.canary_reindex(migration_id, percentage=1.0)

    assert canary_result["sample_count"] == 1
    assert canary_result["passed"] is True  # Good similarity

    # Verify migration updated
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.canary_passed is True
    assert migration.status == MigrationStatus.CANARY_PASSED

    # Step 3: Create snapshot
    snapshot_key = await migration_service.snapshot_qdrant(migration_id)

    assert snapshot_key is not None
    assert "snapshots" in snapshot_key

    # Verify migration updated
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.snapshot_s3_key == snapshot_key

    # Step 4: Backfill documents
    # Add more documents for backfill
    for i in range(5):
        doc = EmbeddingModel(
            id=uuid.uuid4(),
            name="bge-m3-v2",
            dimension=1024,
            is_default=False,
            is_active=True,
            content=f"test document content {i} for backfill",
        )
        session.add(doc)
    await session.commit()

    # Mock embedding service for backfill
    embedding_service.embed_texts.return_value = [
        [0.1, 0.2, 0.3, 0.4] for _ in range(6)  # 6 documents total
    ]

    # Run backfill
    backfill_result = await migration_service.backfill(migration_id, batch_size=3)

    assert backfill_result["processed"] == 6
    assert backfill_result["total"] == 6
    assert backfill_result["speed_docs_per_hour"] > 0

    # Verify migration updated
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.status == MigrationStatus.BACKFILLED
    assert migration.backfill_processed == 6
    assert migration.backfill_total == 6

    # Step 5: Promote migration
    # Mock models for promote
    old_model = await session.get(EmbeddingModel, initial_model.id)
    new_model = await session.get(EmbeddingModel, doc.id)

    await migration_service.promote(migration_id)

    # Verify models updated
    assert old_model.is_default is False
    assert new_model.is_default is True

    # Verify migration updated
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.status == MigrationStatus.PROMOTED
    assert migration.promoted_at is not None

    # Step 6: Test dual-write targets
    targets = get_dual_write_targets(session)
    assert targets is not None
    assert len(targets) == 2
    assert ("documents", "bge-m3-v1") in targets
    assert ("documents_v2", "bge-m3-v2") in targets

    # Step 7: Test active search target
    collection, model = get_active_search_target(session)
    assert collection == "documents_v2"
    assert model == "bge-m3-v2"

    # Step 8: Test rollback
    await migration_service.rollback(migration_id, restore_snapshot=False)

    # Verify models rolled back
    old_model = await session.get(EmbeddingModel, initial_model.id)
    new_model = await session.get(EmbeddingModel, doc.id)
    assert old_model.is_default is True
    assert new_model.is_default is False

    # Verify migration updated
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.status == MigrationStatus.ROLLED_BACK

    # Step 9: Test cleanup (with force)
    await migration_service.cleanup_old_collection(migration_id, force=True)

    # Verify migration updated
    migration = await session.get(ModelMigration, uuid.UUID(migration_id))
    assert migration.status == MigrationStatus.CLEANED
    assert migration.cleaned_at is not None


@pytest.mark.asyncio
async def test_migration_canary_thresholds(session, migration_service, qdrant_client, embedding_service):
    """Test canary similarity thresholds."""
    # Setup initial model
    initial_model = EmbeddingModel(
        id=uuid.uuid4(),
        name="bge-m3-v1",
        dimension=1024,
        is_default=True,
        is_active=True,
    )
    session.add(initial_model)
    await session.commit()

    # Initialize migration
    migration_id = await migration_service.init_migration(
        new_model_name="bge-m3-v2",
        new_dimension=1024,
    )

    # Mock document
    doc_id = uuid.uuid4()
    doc = EmbeddingModel(
        id=doc_id,
        name="bge-m3-v2",
        dimension=1024,
        is_default=False,
        is_active=True,
        content="test document content",
    )
    session.add(doc)
    await session.commit()

    # Test case 1: Good similarity (should pass)
    old_vectors = [
        qdrant_models.PointStruct(
            id=str(doc_id),
            vector=[0.8, 0.6, 0.0],  # Similar to new vector
            payload={"content_hash": "abc123"},
        )
    ]
    qdrant_client.retrieve.return_value = old_vectors
    embedding_service.embed_texts.return_value = [[0.9, 0.7, 0.1]]

    canary_result = await migration_service.canary_reindex(migration_id, percentage=1.0)
    assert canary_result["passed"] is True

    # Test case 2: Low similarity (should fail)
    old_vectors = [
        qdrant_models.PointStruct(
            id=str(doc_id),
            vector=[1.0, 0.0, 0.0],  # Orthogonal to new vector
            payload={"content_hash": "abc123"},
        )
    ]
    qdrant_client.retrieve.return_value = old_vectors
    embedding_service.embed_texts.return_value = [[0.0, 1.0, 0.0]]

    canary_result = await migration_service.canary_reindex(migration_id, percentage=1.0)
    assert canary_result["passed"] is False

    # Test case 3: High average similarity (should fail)
    old_vectors = [
        qdrant_models.PointStruct(
            id=str(doc_id),
            vector=[0.99, 0.99, 0.99],  # Very similar
            payload={"content_hash": "abc123"},
        )
    ]
    qdrant_client.retrieve.return_value = old_vectors
    embedding_service.embed_texts.return_value = [[0.99, 0.99, 0.99]]

    canary_result = await migration_service.canary_reindex(migration_id, percentage=1.0)
    assert canary_result["passed"] is False  # Average > 0.95


@pytest.mark.asyncio
async def test_migration_promotion_guards(session, migration_service, embedding_service):
    """Test promotion guards (preconditions)."""
    # Setup initial model
    initial_model = EmbeddingModel(
        id=uuid.uuid4(),
        name="bge-m3-v1",
        dimension=1024,
        is_default=True,
        is_active=True,
    )
    session.add(initial_model)
    await session.commit()

    # Initialize migration
    migration_id = await migration_service.init_migration(
        new_model_name="bge-m3-v2",
        new_dimension=1024,
    )

    # Test 1: Promote without canary should fail
    with pytest.raises(ValueError, match="Cannot promote without passed canary"):
        await migration_service.promote(migration_id)

    # Mock canary pass
    with patch('app.db.queries.model_migrations.get_migration') as mock_get:
        migration = await session.get(ModelMigration, uuid.UUID(migration_id))
        migration.canary_passed = True
        migration.snapshot_s3_key = "test-snapshot-key"
        migration.backfill_processed = 100
        migration.backfill_total = 100
        mock_get.return_value = migration

        # Test 2: Promote without snapshot should fail
        migration.snapshot_s3_key = None
        with pytest.raises(ValueError, match="Cannot promote without snapshot"):
            await migration_service.promote(migration_id)

        # Test 3: Promote with incomplete backfill should fail
        migration.snapshot_s3_key = "test-snapshot-key"
        migration.backfill_processed = 50
        migration.backfill_total = 100
        with pytest.raises(ValueError, match="Cannot promote before backfill is complete"):
            await migration_service.promote(migration_id)

        # Test 4: Promote with all preconditions should succeed
        migration.backfill_processed = 100
        migration.backfill_total = 100
        # Mock models
        old_model = await session.get(EmbeddingModel, initial_model.id)
        new_model = EmbeddingModel(
            id=uuid.uuid4(),
            name="bge-m3-v2",
            dimension=1024,
            is_default=False,
            is_active=True,
        )
        session.add(new_model)
        await session.commit()

        await migration_service.promote(migration_id)

        # Verify models updated
        old_model = await session.get(EmbeddingModel, initial_model.id)
        new_model = await session.get(EmbeddingModel, new_model.id)
        assert old_model.is_default is False
        assert new_model.is_default is True


@pytest.mark.asyncio
async def test_migration_cleanup_guards(session, migration_service):
    """Test cleanup guards."""
    # Setup promoted migration
    migration = ModelMigration(
        id=uuid.uuid4(),
        old_model_name="bge-m3-v1",
        new_model_name="bge-m3-v2",
        old_collection="documents",
        new_collection="documents_v2",
        new_hf_model="BAAI/bge-m3-v2",
        status=MigrationStatus.PROMOTED,
        promoted_at=datetime.now(),  # Recent promote
    )
    session.add(migration)
    await session.commit()

    # Test 1: Cleanup before stable period should fail
    with pytest.raises(ValueError, match="Cleanup not allowed"):
        await migration_service.cleanup_old_collection(str(migration.id))

    # Test 2: Cleanup with force should succeed
    await migration_service.cleanup_old_collection(str(migration.id), force=True)

    # Verify migration cleaned
    migration = await session.get(ModelMigration, migration.id)
    assert migration.status == MigrationStatus.CLEANED

    # Test 3: Cleanup after stable period should succeed without force
    migration = ModelMigration(
        id=uuid.uuid4(),
        old_model_name="bge-m3-v1",
        new_model_name="bge-m3-v2",
        old_collection="documents",
        new_collection="documents_v2",
        new_hf_model="BAAI/bge-m3-v2",
        status=MigrationStatus.PROMOTED,
        promoted_at=datetime.now() - timedelta(days=settings.migration.stable_days + 1),
    )
    session.add(migration)
    await session.commit()

    await migration_service.cleanup_old_collection(str(migration.id), force=False)

    # Verify migration cleaned
    migration = await session.get(ModelMigration, migration.id)
    assert migration.status == MigrationStatus.CLEANED


@pytest.mark.asyncio
async def test_full_reindex_from_postgres(session, migration_service, qdrant_client):
    """Test full reindex from PostgreSQL."""
    # Add test documents
    documents = []
    for i in range(3):
        doc = EmbeddingModel(
            id=uuid.uuid4(),
            name="bge-m3-v1",
            dimension=1024,
            is_default=True,
            is_active=True,
            content=f"test document content {i}",
        )
        session.add(doc)
        documents.append(doc)
    await session.commit()

    # Mock embedding service
    migration_service.embedding_old.embed_texts = AsyncMock(return_value=[
        [0.1, 0.2, 0.3, 0.4] for _ in range(3)
    ])

    # Run full reindex
    result = await migration_service.full_reindex_from_postgres("test_collection")

    assert result["processed"] == 3
    assert result["total"] == 3
    assert result["collection"] == "test_collection"

    # Verify Qdrant upsert called
    assert qdrant_client.upsert.call_count == 1
    call_args = qdrant_client.upsert.call_args
    assert call_args[1]["collection_name"] == "test_collection"


@pytest.mark.asyncio
async def test_migration_status(session, migration_service):
    """Test migration status reporting."""
    # Setup migration
    migration = ModelMigration(
        id=uuid.uuid4(),
        old_model_name="bge-m3-v1",
        new_model_name="bge-m3-v2",
        old_collection="documents",
        new_collection="documents_v2",
        new_hf_model="BAAI/bge-m3-v2",
        status=MigrationStatus.INIT,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    session.add(migration)
    await session.commit()

    # Get status
    status = await migration_service.status(str(migration.id))

    assert status["id"] == str(migration.id)
    assert status["old_model_name"] == "bge-m3-v1"
    assert status["new_model_name"] == "bge-m3-v2"
    assert status["status"] == MigrationStatus.INIT
    assert status["created_at"] is not None
    assert status["updated_at"] is not None
    assert status["promoted_at"] is None
    assert status["cleaned_at"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
