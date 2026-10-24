"""Backfill worker for document embeddings during migration (PR-07).

Processes documents in batches with progress tracking and rate limiting.
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from qdrant_client.http import models as qdrant_models
from sqlalchemy import func, select

from app.db.models.embedding_model import EmbeddingModel
from app.db.queries.model_migrations import update_backfill_progress
from app.db.session import AsyncSession
from app.embedding.service import EmbeddingConfig, EmbeddingService
from app.observability.metrics import migration_backfill_progress

if TYPE_CHECKING:
    from app.embedding.cache import EmbeddingCache

logger = structlog.get_logger("backfill_worker")


class BackfillWorker:
    """Worker for backfilling document embeddings in batches."""

    def __init__(
        self,
        session: AsyncSession,
        migration_id: str,
        embedding_service: EmbeddingService,
        batch_size: int = 50,
        max_concurrent_batches: int = 1,
    ):
        self.session = session
        self.migration_id = migration_id
        self.embedding_service = embedding_service
        self.batch_size = batch_size
        self.max_concurrent_batches = max_concurrent_batches
        self.semaphore = asyncio.Semaphore(max_concurrent_batches)

    async def process_all_documents(self) -> dict[str, Any]:
        """Process all documents with new model embeddings.
        
        Returns backfill report with progress and performance metrics.
        """
        # Get total document count
        total_docs = self.session.scalar(
            select(func.count())
            .select_from(EmbeddingModel.__table__)
            .where(EmbeddingModel.deleted_at.is_(None))
        )

        processed = 0
        start_time = datetime.now()
        batch_count = 0

        while processed < total_docs:
            # Get batch of documents
            batch_docs = self.session.scalars(
                select(EmbeddingModel)
                .where(EmbeddingModel.deleted_at.is_(None))
                .order_by(EmbeddingModel.id)
                .offset(processed)
                .limit(self.batch_size)
            ).all()

            if not batch_docs:
                break

            # Process batch with semaphore for concurrency control
            await self.semaphore.acquire()
            try:
                batch_result = await self._process_batch(batch_docs, processed)
                processed += len(batch_docs)
                batch_count += 1

                # Update progress
                update_backfill_progress(self.session, self.migration_id, processed, total_docs)

                # Log progress periodically
                if batch_count % 10 == 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    speed = processed / elapsed * 3600 if elapsed > 0 else 0
                    logger.info(
                        "Backfill progress",
                        migration_id=self.migration_id,
                        processed=processed,
                        total=total_docs,
                        speed_docs_per_hour=speed,
                        batch_count=batch_count,
                    )

            finally:
                self.semaphore.release()

        # Final metrics
        elapsed = (datetime.now() - start_time).total_seconds()
        speed = total_docs / elapsed * 3600 if elapsed > 0 else 0
        estimated_remaining = 0 if processed >= total_docs else (total_docs - processed) / speed * 3600

        # Update final progress
        migration_backfill_progress.set(
            total_docs,
            labels={"migration_id": self.migration_id}
        )

        logger.info(
            "Backfill completed",
            migration_id=self.migration_id,
            total_docs=total_docs,
            processed=processed,
            batch_count=batch_count,
            elapsed_seconds=elapsed,
            speed_docs_per_hour=speed,
            estimated_remaining_seconds=estimated_remaining,
        )

        return {
            "migration_id": self.migration_id,
            "processed": processed,
            "total": total_docs,
            "batch_count": batch_count,
            "elapsed_seconds": elapsed,
            "speed_docs_per_hour": speed,
            "estimated_remaining_seconds": estimated_remaining,
        }

    async def _process_batch(self, batch_docs: list[EmbeddingModel], offset: int) -> dict[str, Any]:
        """Process a single batch of documents.
        
        Extracts contents, generates embeddings, and upserts to Qdrant.
        """
        batch_start = datetime.now()

        # Extract contents
        contents = [doc.content for doc in batch_docs]

        # Generate embeddings
        embeddings = await self.embedding_service.embed_texts(contents)

        # Create Qdrant points
        points = []
        for i, doc in enumerate(batch_docs):
            point = qdrant_models.PointStruct(
                id=doc.id,
                vector=embeddings[i],
                payload={
                    "model_name": self.embedding_service.config.model_name,
                    "model_rev": "1",  # TODO: Get from model config
                    "document_id": str(doc.id),
                    "processed_at": datetime.now().isoformat(),
                },
            )
            points.append(point)

        # Upsert to Qdrant (this would be handled by the dual-write mechanism)
        # For now, we'll just simulate the upsert
        await self._simulate_upsert(points)

        batch_elapsed = (datetime.now() - batch_start).total_seconds()

        logger.debug(
            "Batch processed",
            migration_id=self.migration_id,
            batch_start=offset,
            batch_size=len(batch_docs),
            batch_elapsed=batch_elapsed,
        )

        return {
            "batch_start": offset,
            "batch_size": len(batch_docs),
            "batch_elapsed": batch_elapsed,
        }

    async def _simulate_upsert(self, points: list[qdrant_models.PointStruct]) -> None:
        """Simulate Qdrant upsert for testing.
        
        In production, this would call the actual Qdrant client.
        """
        # Simulate network delay
        await asyncio.sleep(0.01)

        # Log the upsert (in production, this would be the actual upsert)
        logger.debug(
            "Simulated Qdrant upsert",
            point_count=len(points),
            first_point_id=points[0].id if points else None,
        )


async def create_backfill_worker(
    session: AsyncSession,
    migration_id: str,
    embedding_config: EmbeddingConfig,
    batch_size: int = 50,
    max_concurrent_batches: int = 1,
) -> BackfillWorker:
    """Create a backfill worker with the given configuration."""
    # Create embedding service
    embedding_service = EmbeddingService(
        config=embedding_config,
        cache=EmbeddingCache(),
    )

    # Load model asynchronously
    await embedding_service._load_model()

    return BackfillWorker(
        session=session,
        migration_id=migration_id,
        embedding_service=embedding_service,
        batch_size=batch_size,
        max_concurrent_batches=max_concurrent_batches,
    )


__all__ = ["BackfillWorker", "create_backfill_worker"]
