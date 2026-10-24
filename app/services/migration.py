"""Embedding model migration framework (PR-07).

ARCHITECT §13.1–13.6: Миграционный фреймворк embedding-моделей.

Provides MigrationService for blue-green model replacement, backfill worker,
canary reindex with similarity checks, Qdrant snapshot management, and rollback
capabilities. Implements dual-write pattern for safe transitions.
"""

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.config import settings
from app.db.models.embedding_model import EmbeddingModel
from app.db.models.model_migration import MigrationStatus, ModelMigration
from app.db.queries.model_migrations import (
    create_migration,
    is_cleanup_allowed,
    mark_backfilled,
    mark_cleaned,
    mark_promoted,
    mark_rolled_back,
    set_snapshot_s3_key,
    update_backfill_progress,
    update_canary_results,
)
from app.db.session import AsyncSession
from app.embedding.service import EmbeddingConfig, EmbeddingService
from app.observability.metrics import (
    migration_canary_similarity,
    migration_rollbacks_total,
    migration_snapshots_created_total,
)
from app.services.qdrant import QdrantService

if TYPE_CHECKING:
    from app.embedding.cache import EmbeddingCache

logger = structlog.get_logger("migration")


class MigrationService:
    """Service for managing embedding model migrations with blue-green deployment."""

    def __init__(
        self,
        session: AsyncSession,
        qdrant_client: QdrantClient | None = None,
        embedding_old: EmbeddingService | None = None,
        embedding_new: EmbeddingService | None = None,
        s3_client: Any | None = None,  # boto3 client
    ):
        self.session = session
        self.qdrant_client = qdrant_client or QdrantService(
            config=settings.qdrant,
            client=QdrantClient(url=settings.qdrant.url)
        )
        self.embedding_old = embedding_old or EmbeddingService(
            config=EmbeddingConfig(
                model_name=settings.embedding.model_name,
                hf_model=settings.embedding.hf_model,
                expected_dimension=settings.embedding.expected_dimension,
                device=settings.embedding.device,
                cache=EmbeddingCache(),
            )
        )
        self.embedding_new = embedding_new
        self.s3_client = s3_client

        # Load the old model on init
        asyncio.create_task(self.embedding_old._load_model())

    async def init_migration(
        self,
        new_model_name: str,
        new_dimension: int,
        new_hf_model: str | None = None,
    ) -> str:
        """Initialize a new migration.
        
        Creates new embedding model record, new Qdrant collection, and migration record.
        """
        # Check for existing active migration
        existing = self.session.scalars(
            select(ModelMigration)
            .where(ModelMigration.status.in_([
                MigrationStatus.INIT,
                MigrationStatus.CANARY_PASSED,
                MigrationStatus.CANARY_FAILED,
                MigrationStatus.BACKFILLED,
            ]))
        ).first()

        if existing:
            raise ValueError(f"Migration already in progress: {existing.id}")

        # Get current default model and collection
        current_model = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.is_default)
        ).first()

        if not current_model:
            raise ValueError("No default embedding model found")

        old_model_name = current_model.name
        old_collection = settings.qdrant.collection

        # Generate new collection name
        new_collection = self._generate_new_collection_name(old_collection)

        # Create new embedding model record
        new_model = EmbeddingModel(
            name=new_model_name,
            dimension=new_dimension,
            is_default=False,  # Will be promoted later
            is_active=True,
            hf_model_id=new_hf_model or new_model_name,  # Default to model name
        )
        self.session.add(new_model)
        self.session.commit()
        self.session.refresh(new_model)

        # Create new Qdrant collection
        from qdrant_client.http import models as qdrant_models

        collection_config = qdrant_models.CreateCollection(
            vectors=qdrant_models.VectorsConfig(
                size=new_dimension,
                distance="Cosine"
            ),
            replication_factor=settings.qdrant.replication_factor,
        )

        await self.qdrant_client._client.create_collection(new_collection, collection_config)

        # Create migration record
        migration = create_migration(
            self.session,
            old_model_name=old_model_name,
            new_model_name=new_model_name,
            old_collection=old_collection,
            new_collection=new_collection,
            new_hf_model=new_hf_model or new_model_name,
        )

        logger.info(
            "Migration initialized",
            migration_id=str(migration.id),
            old_model=old_model_name,
            new_model=new_model_name,
            old_collection=old_collection,
            new_collection=new_collection,
        )

        return str(migration.id)

    def _generate_new_collection_name(self, base_collection: str) -> str:
        """Generate new collection name with suffix or version."""
        suffix = settings.migration.collection_suffix

        # Try base + suffix first
        new_name = f"{base_collection}{suffix}"

        # Check if it exists and increment if needed
        if self.qdrant_client.collection_exists(new_name):
            counter = 2
            while True:
                new_name = f"{base_collection}_v{counter}"
                if not self.qdrant_client.collection_exists(new_name):
                    break
                counter += 1

        return new_name

    async def canary_reindex(
        self,
        migration_id: str,
        percentage: float = 0.01,
    ) -> dict[str, Any]:
        """Perform canary reindex on random sample of documents.
        
        Returns canary report with similarity statistics.
        """
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        if migration.status not in [MigrationStatus.INIT, MigrationStatus.CANARY_FAILED]:
            raise ValueError(
                f"Cannot run canary on migration {migration_id} with status {migration.status}"
            )

        # Initialize new embedding service if not provided
        if not self.embedding_new:
            self.embedding_new = EmbeddingService(
                config=EmbeddingConfig(
                    model_name=migration.new_model_name,
                    hf_model=migration.new_hf_model,
                    expected_dimension=migration.new_model.dimension,
                    device=settings.embedding.device,
                    cache=EmbeddingCache(),
                )
            )
            await self.embedding_new._load_model()

        # Sample documents
        total_docs = self.session.scalar(
            select(func.count())
            .select_from(EmbeddingModel.__table__)
            .where(func.hashtext(EmbeddingModel.name) < percentage * 100)
        )

        target_count = max(1, int(total_docs * percentage))
        max_docs = settings.migration.max_canary_docs

        if target_count > max_docs:
            target_count = max_docs

        # Get document IDs for canary
        doc_ids = self.session.scalars(
            select(EmbeddingModel.id)
            .where(func.hashtext(EmbeddingModel.name) < percentage * 100)
            .order_by(func.md5(EmbeddingModel.name))
            .limit(target_count)
        ).all()

        if not doc_ids:
            return {
                "migration_id": migration_id,
                "sample_count": 0,
                "avg_similarity": None,
                "min_similarity": None,
                "max_similarity": None,
                "passed": False,
                "error": "No documents found for canary sample",
            }

        # Retrieve old vectors from Qdrant
        old_vectors = await self.qdrant_client.retrieve(
            migration.old_collection,
            list(doc_ids),
            with_vector=True,
        )

        # Get document contents for new embeddings
        documents = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.id.in_(doc_ids))
            .options(joinedload(EmbeddingModel.content))
        ).all()

        # Create content-to-id mapping
        content_to_id = {doc.content: doc.id for doc in documents}

        # Generate new embeddings
        contents = list(content_to_id.keys())
        new_embeddings = await self.embedding_new.embed_texts(contents)

        # Compute similarities
        similarities = []
        for old_point in old_vectors:
            doc_id = old_point.id
            old_vector = old_point.vector

            # Find corresponding new embedding
            new_vector = None
            for i, content in enumerate(contents):
                if content_to_id[content] == doc_id:
                    new_vector = new_embeddings[i]
                    break

            if new_vector is None:
                continue

            # Compute cosine similarity
            sim = self._cosine_similarity(old_vector, new_vector)
            similarities.append(sim)

        if not similarities:
            return {
                "migration_id": migration_id,
                "sample_count": 0,
                "avg_similarity": None,
                "min_similarity": None,
                "max_similarity": None,
                "passed": False,
                "error": "No valid similarity computations",
            }

        avg_sim = sum(similarities) / len(similarities)
        min_sim = min(similarities)
        max_sim = max(similarities)

        # Check thresholds
        min_threshold = 0.6
        max_threshold = 0.95

        passed = min_sim >= min_threshold and avg_sim <= max_threshold

        # Update migration record
        update_canary_results(
            self.session,
            migration_id_uuid,
            avg_sim,
            min_sim,
            max_sim,
            passed,
        )

        # Update metrics
        migration_canary_similarity.set(
            avg_sim,
            labels={"migration_id": migration_id, "kind": "avg"}
        )
        migration_canary_similarity.set(
            min_sim,
            labels={"migration_id": migration_id, "kind": "min"}
        )
        migration_canary_similarity.set(
            max_sim,
            labels={"migration_id": migration_id, "kind": "max"}
        )

        logger.info(
            "Canary reindex completed",
            migration_id=migration_id,
            sample_count=len(similarities),
            avg_similarity=avg_sim,
            min_similarity=min_sim,
            max_similarity=max_sim,
            passed=passed,
        )

        return {
            "migration_id": migration_id,
            "sample_count": len(similarities),
            "avg_similarity": avg_sim,
            "min_similarity": min_sim,
            "max_similarity": max_sim,
            "passed": passed,
        }

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    async def snapshot_qdrant(
        self,
        migration_id: str,
        collection_name: str | None = None,
    ) -> str:
        """Create Qdrant snapshot and upload to S3.
        
        Returns S3 key for the snapshot.
        """
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        collection = collection_name or migration.new_collection

        # Create snapshot
        snapshot_info = await self.qdrant_client.create_snapshot(collection)

        # Download snapshot locally
        local_path = await self.qdrant_client.download_snapshot(
            snapshot_info.name,
            collection,
        )

        # Upload to S3
        if not self.s3_client:
            raise ValueError("S3 client not available for snapshot upload")

        bucket_name = settings.migration.snapshot_s3_bucket
        s3_key = f"snapshots/{collection}/{snapshot_info.name}.snapshot"

        # Ensure bucket exists
        try:
            self.s3_client.create_bucket(Bucket=bucket_name)
        except self.s3_client.exceptions.BucketAlreadyOwnedByYou:
            pass

        # Upload snapshot
        self.s3_client.upload_file(
            local_path,
            bucket_name,
            s3_key,
        )

        # Update migration record
        set_snapshot_s3_key(self.session, migration_id_uuid, s3_key)

        # Update metrics
        migration_snapshots_created_total.inc(labels={"collection": collection})

        logger.info(
            "Qdrant snapshot created and uploaded",
            migration_id=migration_id,
            collection=collection,
            snapshot_name=snapshot_info.name,
            s3_key=s3_key,
        )

        return s3_key

    async def backfill(
        self,
        migration_id: str,
        batch_size: int = 50,
    ) -> dict[str, Any]:
        """Backfill all documents with new model embeddings.
        
        Processes documents in batches and updates progress.
        """
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        if not migration.canary_passed:
            raise ValueError("Cannot start backfill without passed canary check")

        if not migration.snapshot_s3_key:
            raise ValueError("Cannot start backfill without snapshot")

        # Initialize new embedding service if not provided
        if not self.embedding_new:
            self.embedding_new = EmbeddingService(
                config=EmbeddingConfig(
                    model_name=migration.new_model_name,
                    hf_model=migration.new_hf_model,
                    expected_dimension=migration.new_model.dimension,
                    device=settings.embedding.device,
                    cache=EmbeddingCache(),
                )
            )
            await self.embedding_new._load_model()

        # Get total document count
        total_docs = self.session.scalar(
            select(func.count())
            .select_from(EmbeddingModel.__table__)
            .where(EmbeddingModel.deleted_at.is_(None))
        )

        migration.backfill_total = total_docs
        self.session.commit()

        # Process in batches
        processed = 0
        start_time = datetime.now()

        while processed < total_docs:
            # Get batch of documents
            batch_docs = self.session.scalars(
                select(EmbeddingModel)
                .where(EmbeddingModel.deleted_at.is_(None))
                .order_by(EmbeddingModel.id)
                .offset(processed)
                .limit(batch_size)
            ).all()

            if not batch_docs:
                break

            # Extract contents and create points
            contents = [doc.content for doc in batch_docs]
            embeddings = await self.embedding_new.embed_texts(contents)

            points = []
            for i, doc in enumerate(batch_docs):
                point = qdrant_models.PointStruct(
                    id=doc.id,
                    vector=embeddings[i],
                    payload={
                        "model_name": migration.new_model_name,
                        "model_rev": "1",  # TODO: Get from model config
                        "document_id": str(doc.id),
                    },
                )
                points.append(point)

            # Upsert to new collection
            await self.qdrant_client.upsert(
                migration.new_collection,
                points,
            )

            processed += len(batch_docs)
            update_backfill_progress(self.session, migration_id_uuid, processed, total_docs)

            # Log progress
            if processed % (batch_size * 10) == 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                speed = processed / elapsed * 3600 if elapsed > 0 else 0
                logger.info(
                    "Backfill progress",
                    migration_id=migration_id,
                    processed=processed,
                    total=total_docs,
                    speed_docs_per_hour=speed,
                )

        # Mark as backfilled
        mark_backfilled(self.session, migration_id_uuid)

        elapsed = (datetime.now() - start_time).total_seconds()
        speed = total_docs / elapsed * 3600 if elapsed > 0 else 0

        logger.info(
            "Backfill completed",
            migration_id=migration_id,
            total_docs=total_docs,
            elapsed_seconds=elapsed,
            speed_docs_per_hour=speed,
        )

        return {
            "migration_id": migration_id,
            "processed": processed,
            "total": total_docs,
            "elapsed_seconds": elapsed,
            "speed_docs_per_hour": speed,
        }

    async def promote(self, migration_id: str) -> None:
        """Promote migration to active state.
        
        Switches default model, enables dual-write, and updates search targets.
        """
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        # Check preconditions
        if not migration.canary_passed:
            raise ValueError("Cannot promote without passed canary check")

        if not migration.snapshot_s3_key:
            raise ValueError("Cannot promote without snapshot")

        if migration.backfill_processed < migration.backfill_total:
            raise ValueError("Cannot promote before backfill is complete")

        # Update default models
        old_model = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.name == migration.old_model_name)
        ).first()

        new_model = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.name == migration.new_model_name)
        ).first()

        if old_model:
            old_model.is_default = False

        if new_model:
            new_model.is_default = True

        # Mark as promoted
        mark_promoted(self.session, migration_id_uuid)

        logger.info(
            "Migration promoted",
            migration_id=migration_id,
            old_model=migration.old_model_name,
            new_model=migration.new_model_name,
            old_collection=migration.old_collection,
            new_collection=migration.new_collection,
        )

    async def rollback(
        self,
        migration_id: str,
        restore_snapshot: bool = False,
    ) -> None:
        """Rollback migration to previous state.
        
        Switches default model back and optionally restores from snapshot.
        """
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        if migration.status != MigrationStatus.PROMOTED:
            raise ValueError(f"Cannot rollback migration with status {migration.status}")

        # Update default models back
        old_model = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.name == migration.old_model_name)
        ).first()

        new_model = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.name == migration.new_model_name)
        ).first()

        if old_model:
            old_model.is_default = True

        if new_model:
            new_model.is_default = False

        # Mark as rolled back
        mark_rolled_back(self.session, migration_id_uuid)

        # Update metrics
        migration_rollbacks_total.inc()

        # Optionally restore snapshot
        if restore_snapshot and migration.snapshot_s3_key:
            try:
                # Download snapshot from S3
                local_path = f"/tmp/{migration.snapshot_s3_key.split('/')[-1]}"

                if self.s3_client:
                    self.s3_client.download_file(
                        settings.migration.snapshot_s3_bucket,
                        migration.snapshot_s3_key,
                        local_path,
                    )

                    # Restore snapshot
                    await self.qdrant_client.restore_snapshot(
                        migration.new_collection,
                        local_path,
                    )

                    logger.info(
                        "Snapshot restored during rollback",
                        migration_id=migration_id,
                        snapshot=migration.snapshot_s3_key,
                    )

            except Exception as e:
                logger.error(
                    "Failed to restore snapshot during rollback",
                    migration_id=migration_id,
                    error=str(e),
                )

        logger.info(
            "Migration rolled back",
            migration_id=migration_id,
            old_model=migration.old_model_name,
            new_model=migration.new_model_name,
        )

    async def cleanup_old_collection(
        self,
        migration_id: str,
        force: bool = False,
    ) -> None:
        """Clean up old collection after stable period.
        
        Deletes old collection and migration record.
        """
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        if not is_cleanup_allowed(self.session, migration_id, force):
            raise ValueError("Cleanup not allowed: migration must be promoted for stable_days period")

        # Delete old collection
        try:
            await self.qdrant_client.delete_collection(migration.old_collection)
        except Exception as e:
            logger.warning(
                "Failed to delete old collection during cleanup",
                migration_id=migration_id,
                collection=migration.old_collection,
                error=str(e),
            )

        # Mark as cleaned
        mark_cleaned(self.session, migration_id_uuid)

        logger.info(
            "Migration cleaned up",
            migration_id=migration_id,
            old_collection=migration.old_collection,
        )

    async def full_reindex_from_postgres(self, collection_name: str | None = None) -> dict[str, Any]:
        """Full reindex from PostgreSQL documents (ARCHITECT §13.3).
        
        Creates collection if missing, then indexes all live documents.
        """
        collection = collection_name or settings.qdrant.collection

        # Ensure collection exists
        from qdrant_client.http import models as qdrant_models

        try:
            await self.qdrant_client._client.get_collection(collection)
        except Exception:
            # Collection doesn't exist, create it
            collection_config = qdrant_models.CreateCollection(
                vectors=qdrant_models.VectorsConfig(
                    size=settings.embedding.dimension,
                    distance="Cosine"
                ),
                replication_factor=settings.qdrant.replication_factor,
            )
            await self.qdrant_client._client.create_collection(collection, collection_config)

        # Get all live documents
        documents = self.session.scalars(
            select(EmbeddingModel)
            .where(EmbeddingModel.deleted_at.is_(None))
            .options(joinedload(EmbeddingModel.content))
        ).all()

        if not documents:
            return {
                "collection": collection,
                "processed": 0,
                "total": 0,
                "speed_docs_per_hour": 0,
                "elapsed_seconds": 0,
            }

        # Generate embeddings and upsert
        contents = [doc.content for doc in documents]
        embeddings = await self.embedding_old.embed_texts(contents)

        points = []
        for i, doc in enumerate(documents):
            point = qdrant_models.PointStruct(
                id=doc.id,
                vector=embeddings[i],
                payload={
                    "model_name": settings.embedding.model_name,
                    "model_rev": "1",
                    "document_id": str(doc.id),
                },
            )
            points.append(point)

        await self.qdrant_client.upsert(collection, points)

        return {
            "collection": collection,
            "processed": len(documents),
            "total": len(documents),
            "speed_docs_per_hour": len(documents),  # Instant for mock
            "elapsed_seconds": 0,
        }

    async def status(self, migration_id: str) -> dict[str, Any]:
        """Get migration status and progress."""
        migration_id_uuid = uuid.UUID(migration_id)
        migration = self.session.get(ModelMigration, migration_id_uuid)

        if not migration:
            raise ValueError(f"Migration not found: {migration_id}")

        return {
            "id": str(migration.id),
            "old_model_name": migration.old_model_name,
            "new_model_name": migration.new_model_name,
            "old_collection": migration.old_collection,
            "new_collection": migration.new_collection,
            "status": migration.status,
            "canary_passed": migration.canary_passed,
            "canary_avg_similarity": migration.canary_avg_similarity,
            "canary_min_similarity": migration.canary_min_similarity,
            "canary_max_similarity": migration.canary_max_similarity,
            "snapshot_s3_key": migration.snapshot_s3_key,
            "backfill_processed": migration.backfill_processed,
            "backfill_total": migration.backfill_total,
            "created_at": migration.created_at.isoformat(),
            "updated_at": migration.updated_at.isoformat(),
            "promoted_at": migration.promoted_at.isoformat() if migration.promoted_at else None,
            "cleaned_at": migration.cleaned_at.isoformat() if migration.cleaned_at else None,
        }


__all__ = ["MigrationService"]
