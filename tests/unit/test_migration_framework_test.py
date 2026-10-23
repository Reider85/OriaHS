"""Unit tests for migration framework (PR-07).

Tests MigrationService, canary checks, backfill worker, and migration queries
without requiring Docker or external services.
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.models import PointStruct
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.embedding_model import EmbeddingModel
from app.db.models.model_migration import MigrationStatus, ModelMigration
from app.db.queries.model_migrations import (
    create_migration,
    get_active_search_target,
    get_dual_write_targets,
    is_cleanup_allowed,
    update_backfill_progress,
    update_canary_results,
)
from app.embedding.service import EmbeddingService
from app.migration import MigrationService
from app.migration.backfill_worker import BackfillWorker
from app.migration.canary_check import compute_canary_report
from app.services.qdrant import QdrantService


class TestMigrationService:
    """Test MigrationService core functionality."""

    @pytest.fixture
    def session(self):
        """Create a mock database session."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def qdrant_client(self):
        """Create a mock Qdrant client."""
        client = AsyncMock()
        client.collection_exists = AsyncMock(return_value=False)
        client.create_collection = AsyncMock()
        client.upsert = AsyncMock()
        client.delete_collection = AsyncMock()
        return client

    @pytest.fixture
    def embedding_service(self):
        """Create a mock embedding service."""
        service = AsyncMock(spec=EmbeddingService)
        service.embed_texts = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        return service

    @pytest.fixture
    def migration_service(self, session, qdrant_client, embedding_service):
        """Create MigrationService with mocked dependencies."""
        return MigrationService(
            session=session,
            qdrant_client=qdrant_client,
            embedding_old=embedding_service,
            embedding_new=embedding_service,
        )

    @pytest.mark.asyncio
    async def test_init_migration(self, migration_service, session):
        """Test migration initialization."""
        # Mock existing model
        existing_model = EmbeddingModel(
            id=uuid.uuid4(),
            name="bge-m3-v1",
            dimension=1024,
            is_default=True,
            is_active=True,
        )
        session.scalars.return_value.first.return_value = existing_model

        # Mock commit and refresh
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        # Run init
        migration_id = await migration_service.init_migration(
            new_model_name="bge-m3-v2",
            new_dimension=1024,
            new_hf_model="BAAI/bge-m3-v2",
        )

        # Verify migration created
        assert migration_id is not None
        session.add.assert_called_once()
        session.commit.assert_called_once()

        # Verify Qdrant collection created
        migration_service.qdrant_client.create_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_migration_existing_error(self, migration_service, session):
        """Test error when migration already in progress."""
        # Mock existing migration
        existing_migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.INIT,
        )
        session.scalars.return_value.first.return_value = existing_migration

        # Should raise error
        with pytest.raises(ValueError, match="Migration already in progress"):
            await migration_service.init_migration(
                new_model_name="bge-m3-v3",
                new_dimension=1024,
            )

    @pytest.mark.asyncio
    async def test_canary_reindex(self, migration_service, session):
        """Test canary reindex computation."""
        # Mock migration
        migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.INIT,
        )
        session.get.return_value = migration

        # Mock document retrieval
        doc_id = uuid.uuid4()
        session.scalars.return_value.all.return_value = [doc_id]

        # Mock Qdrant retrieve
        old_vectors = [
            qdrant_models.PointStruct(
                id=str(doc_id),
                vector=[0.1, 0.2, 0.3],
                payload={"content_hash": "abc123"},
            )
        ]
        migration_service.qdrant_client.retrieve.return_value = old_vectors

        # Mock embedding service
        migration_service.embedding_new.embed_texts.return_value = [[0.2, 0.3, 0.4]]

        # Mock update_canary_results
        with patch('app.db.queries.model_migrations.update_canary_results') as mock_update:
            # Run canary
            result = await migration_service.canary_reindex(
                str(migration.id),
                percentage=0.01,
            )

            # Verify results
            assert result["migration_id"] == str(migration.id)
            assert result["sample_count"] == 1
            assert result["avg_similarity"] is not None
            assert result["min_similarity"] is not None
            assert result["max_similarity"] is not None
            assert isinstance(result["passed"], bool)

            # Verify update called
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_canary_reindex_no_docs(self, migration_service, session):
        """Test canary reindex with no documents."""
        # Mock migration
        migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.INIT,
        )
        session.get.return_value = migration

        # Mock no documents
        session.scalars.return_value.all.return_value = []

        # Run canary
        result = await migration_service.canary_reindex(
            str(migration.id),
            percentage=0.01,
        )

        # Verify error result
        assert result["sample_count"] == 0
        assert result["passed"] is False
        assert result["error"] == "No documents found for canary sample"

    @pytest.mark.asyncio
    async def test_promote_preconditions(self, migration_service, session):
        """Test promote with missing preconditions."""
        # Mock migration without canary
        migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.INIT,
            canary_passed=None,
        )
        session.get.return_value = migration

        # Should raise error
        with pytest.raises(ValueError, match="Cannot promote without passed canary"):
            await migration_service.promote(str(migration.id))

    @pytest.mark.asyncio
    async def test_promote_success(self, migration_service, session):
        """Test successful promote."""
        # Mock migration with canary passed
        migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.BACKFILLED,
            canary_passed=True,
            backfill_processed=100,
            backfill_total=100,
        )
        session.get.return_value = migration

        # Mock models
        old_model = EmbeddingModel(
            id=uuid.uuid4(),
            name="bge-m3-v1",
            dimension=1024,
            is_default=True,
            is_active=True,
        )
        new_model = EmbeddingModel(
            id=uuid.uuid4(),
            name="bge-m3-v2",
            dimension=1024,
            is_default=False,
            is_active=True,
        )
        session.scalars.return_value.first.side_effect = [old_model, new_model]

        # Mock mark_promoted
        with patch('app.db.queries.model_migrations.mark_promoted') as mock_mark:
            # Run promote
            await migration_service.promote(str(migration.id))

            # Verify models updated
            assert old_model.is_default is False
            assert new_model.is_default is True

            # Verify mark called
            mock_mark.assert_called_once_with(session, migration.id)


class TestCanaryCheck:
    """Test canary check functionality."""

    def test_compute_canary_report_good_similarity(self):
        """Test canary report with good similarity scores."""
        old_vectors = [
            qdrant_models.PointStruct(id="1", vector=[0.1, 0.2, 0.3]),
            qdrant_models.PointStruct(id="2", vector=[0.2, 0.3, 0.4]),
        ]
        new_embeddings = [[0.2, 0.3, 0.4], [0.3, 0.4, 0.5]]
    
        report = compute_canary_report(old_vectors, new_embeddings, max_threshold=1.0)
    
        assert report.sample_count == 2
        assert report.avg_similarity is not None
        assert report.min_similarity is not None
        assert report.max_similarity is not None
        assert report.passed is True  # Should pass with good similarity

    def test_compute_canary_report_bad_similarity(self):
        """Test canary report with bad similarity scores."""
        old_vectors = [
            qdrant_models.PointStruct(id="1", vector=[1.0, 0.0, 0.0]),
            qdrant_models.PointStruct(id="2", vector=[0.0, 1.0, 0.0]),
        ]
        new_embeddings = [[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]  # Orthogonal vectors

        report = compute_canary_report(old_vectors, new_embeddings)

        assert report.sample_count == 2
        assert report.avg_similarity == 0.0  # Orthogonal vectors have 0 similarity
        assert report.passed is False  # Should fail with 0 similarity

    def test_compute_canary_report_empty_inputs(self):
        """Test canary report with empty inputs."""
        report = compute_canary_report([], [])

        assert report.sample_count == 0
        assert report.passed is False
        assert report.error == "No vectors or embeddings provided"


class TestBackfillWorker:
    """Test backfill worker functionality."""

    @pytest.fixture
    def session(self):
        """Create a mock database session."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def embedding_service(self):
        """Create a mock embedding service."""
        service = AsyncMock(spec=EmbeddingService)
        service.embed_texts = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        return service

    @pytest.mark.asyncio
    async def test_backfill_worker_process_all(self, session, embedding_service):
        """Test backfill worker processing all documents."""
        # Mock documents
        doc1 = MagicMock(id=uuid.uuid4(), content="test content 1")
        doc2 = MagicMock(id=uuid.uuid4(), content="test content 2")
        session.scalars.return_value.all.return_value = [doc1, doc2]

        # Mock total count
        session.scalar.return_value = 2

        # Create worker
        worker = BackfillWorker(
            session=session,
            migration_id="test-migration-id",
            embedding_service=embedding_service,
            batch_size=1,
        )

        # Mock update_backfill_progress
        with patch('app.db.queries.model_migrations.update_backfill_progress') as mock_update:
            # Process documents
            result = await worker.process_all_documents()

            # Verify results
            assert result["processed"] == 2
            assert result["total"] == 2
            assert result["batch_count"] == 2
            assert result["elapsed_seconds"] >= 0

            # Verify embeddings called
            assert embedding_service.embed_texts.call_count == 2

            # Verify progress updates
            assert mock_update.call_count == 2


class TestMigrationQueries:
    """Test migration database queries."""

    @pytest.fixture
    def session(self):
        """Create a mock database session."""
        return AsyncMock(spec=AsyncSession)

    def test_create_migration(self, session):
        """Test creating a migration record."""
        migration = create_migration(
            session,
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
        )

        assert migration.old_model_name == "bge-m3-v1"
        assert migration.new_model_name == "bge-m3-v2"
        assert migration.status == MigrationStatus.INIT
        session.add.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_dual_write_targets(self, session):
        """Test getting dual write targets."""
        # Mock promoted migration
        migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.PROMOTED,
        )
        session.scalars.return_value.first.return_value = migration

        targets = get_dual_write_targets(session)

        assert targets is not None
        assert len(targets) == 2
        assert ("documents", "bge-m3-v1") in targets
        assert ("documents_v2", "bge-m3-v2") in targets

    @pytest.mark.asyncio
    async def test_get_dual_write_targets_none(self, session):
        """Test no dual write targets when no promoted migration."""
        session.scalars.return_value.first.return_value = None

        targets = get_dual_write_targets(session)

        assert targets is None

    @pytest.mark.asyncio
    async def test_get_active_search_target(self, session):
        """Test getting active search target."""
        # Mock promoted migration
        migration = ModelMigration(
            id=uuid.uuid4(),
            old_model_name="bge-m3-v1",
            new_model_name="bge-m3-v2",
            old_collection="documents",
            new_collection="documents_v2",
            new_hf_model="BAAI/bge-m3-v2",
            status=MigrationStatus.PROMOTED,
        )
        session.scalars.return_value.first.return_value = migration

        collection, model = get_active_search_target(session)

        assert collection == "documents_v2"
        assert model == "bge-m3-v2"

    @pytest.mark.asyncio
    async def test_get_active_search_target_fallback(self, session):
        """Test fallback to default when no promoted migration."""
        session.scalars.return_value.first.return_value = None

        # Mock settings
        with patch('app.db.queries.model_migrations.settings') as mock_settings:
            mock_settings.qdrant.collection = "documents"
            mock_settings.embedding.model_name = "bge-m3-v1"

            collection, model = get_active_search_target(session)

            assert collection == "documents"
            assert model == "bge-m3-v1"

    def test_is_cleanup_allowed(self, session):
        """Test cleanup permission check."""
        # Mock promoted migration
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
        session.get.return_value = migration

        # Should be allowed (past stable period)
        assert is_cleanup_allowed(session, migration.id) is True

        # Should not be allowed with force=False and recent promote
        migration.promoted_at = datetime.now()
        assert is_cleanup_allowed(session, migration.id, force=False) is False

        # Should be allowed with force=True
        assert is_cleanup_allowed(session, migration.id, force=True) is True

    def test_update_canary_results(self, session):
        """Test updating canary results."""
        migration_id = uuid.uuid4()

        update_canary_results(
            session,
            migration_id,
            avg_similarity=0.85,
            min_similarity=0.75,
            max_similarity=0.95,
            passed=True,
        )

        # Verify update called
        update_stmt = session.execute.call_args[0][0]

        assert "canary_avg_similarity" in str(update_stmt)
        assert "canary_passed" in str(update_stmt)

    def test_update_backfill_progress(self, session):
        """Test updating backfill progress."""
        migration_id = uuid.uuid4()

        update_backfill_progress(session, migration_id, processed=50, total=100)

        # Verify update called
        update_stmt = session.execute.call_args[0][0]
        assert "backfill_processed" in str(update_stmt)
        assert "backfill_total" in str(update_stmt)

    class TestQdrantService:
        """Test QdrantService multi-collection operations."""

    @pytest.fixture
    def qdrant_client(self):
        """Create a mock Qdrant client."""
        client = AsyncMock()
        client.collection_exists = AsyncMock(return_value=True)
        client.get_collection = AsyncMock()
        client.upsert = AsyncMock()
        client.delete = AsyncMock()
        client.retrieve = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def qdrant_service(self, qdrant_client):
        """Create QdrantService with mocked client."""
        with patch('app.services.qdrant.get_async_qdrant_client', return_value=qdrant_client):
            return QdrantService()

    @pytest.mark.asyncio
    async def test_upsert_point_with_collection(self, qdrant_service, qdrant_client):
        """Test upsert point with specific collection."""
        doc_id = uuid.uuid4()
        vector = [0.1, 0.2, 0.3]
        payload = {"test": "payload"}
        collection_name = "test_collection"

        await qdrant_service.upsert_point(doc_id, vector, payload, collection_name)

        # Verify upsert called with correct collection
        qdrant_client.upsert.assert_called_once()

        call_args = qdrant_client.upsert.call_args
        assert call_args[1]["collection_name"] == collection_name
        assert call_args[1]["points"][0].vector == vector
        assert call_args[1]["points"][0].payload == payload

    @pytest.mark.asyncio
    async def test_delete_point_with_collection(self, qdrant_service, qdrant_client):
        """Test delete point with specific collection."""
        doc_id = uuid.uuid4()
        collection_name = "test_collection"

        await qdrant_service.delete_point(doc_id, collection_name)

        # Verify delete called with correct collection
        qdrant_client.delete.assert_called_once()
        call_args = qdrant_client.delete.call_args
        assert call_args[1]["collection_name"] == collection_name

    @pytest.mark.asyncio
    async def test_collection_exists(self, qdrant_service, qdrant_client):
        """Test collection existence check."""
        qdrant_client.collection_exists = AsyncMock(return_value=True)

        result = await qdrant_service.collection_exists("test_collection")

        assert result is True
        qdrant_client.collection_exists.assert_called_once_with("test_collection")

    @pytest.mark.asyncio
    async def test_retrieve_points(self, qdrant_service, qdrant_client):
        """Test retrieving points from collection."""
        doc_id = uuid.uuid4()
        collection_name = "test_collection"

        # Mock response
        qdrant_client.retrieve.return_value = [
            PointStruct(
                id=doc_id,
                vector=[0.1, 0.2, 0.3],
                payload={"test": "payload"}
            )
        ]

        result = await qdrant_service.retrieve_points(
            [doc_id],
            collection_name,
            with_vector=True,
            with_payload=True,
        )

        # Verify retrieve called with correct parameters
        qdrant_client.retrieve.assert_called_once()

        call_args = qdrant_client.retrieve.call_args
        assert call_args[1]["collection_name"] == collection_name
        assert call_args[1]["ids"] == [doc_id]
        assert call_args[1]["with_vector"] is True
        assert call_args[1]["with_payload"] is True

        # Verify result
        assert len(result) == 1
        assert result[0].id == str(doc_id)

if __name__ == "__main__":
    pytest.main([__file__])
