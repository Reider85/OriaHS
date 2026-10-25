"""Outbox reconciler worker (ARCHITECT §4.4, ROADMAP §3.2.5; P-13).

Every ``poll_interval_seconds`` (30 s) the worker claims a batch of
pending/failed ``search_outbox`` rows with ``FOR UPDATE SKIP LOCKED``,
processes them (embed → Qdrant upsert / Qdrant delete) with bounded
parallelism, and either marks them ``done`` or schedules an exponential
backoff retry. After ``max_attempts`` (20) failures a row goes ``dead`` and
is excluded from future polls (RECONCILER-gate, ROADMAP §3.3).

Run standalone: ``python -m app.reconciler.worker``. Graceful shutdown on
SIGTERM/SIGINT completes the in-flight batch, then exits with code 0.
"""

import asyncio
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

import numpy as np
from qdrant_client.http import models as qmodels
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import EmbeddingConfig, ReconcilerConfig, settings
from app.db.models import Document
from app.db.queries import documents as documents_queries
from app.db.queries import outbox as outbox_queries
from app.db.queries.embedding_models import get_active_model_by_name
from app.db.queries.model_migrations import get_dual_write_targets
from app.db.queries.outbox import PendingOutboxRow
from app.db.redis_client import get_redis_client
from app.db.session import async_session_factory
from app.embedding.cache import EmbeddingCache, should_skip_upsert
from app.embedding.service import EmbeddingService
from app.observability import metrics
from app.observability.logging import get_logger
from app.search.qdrant_payload import QdrantPayload
from app.services.qdrant import QdrantService

logger = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _classify_qdrant_error(exc: Exception) -> str:
    """Map an exception to a Prometheus error_type label."""
    msg = str(exc).lower()
    if "timeout" in msg:
        return "timeout"
    if "unavailable" in msg or "connection" in msg or "refused" in msg:
        return "unavailable"
    if "valid" in msg or "payload" in msg:
        return "validation"
    return "other"


class ReconcilerWorker:
    """Background outbox → Qdrant sync loop (ARCHITECT §4.4)."""

    def __init__(
        self,
        config: ReconcilerConfig | None = None,
        session_factory: SessionFactory | None = None,
        qdrant_service: QdrantService | None = None,
        embedding_service: EmbeddingService | None = None,
        embedding_cache: EmbeddingCache | None = None,
    ) -> None:
        self._config = config or settings.reconciler
        self._session_factory: SessionFactory = session_factory or async_session_factory
        self._qdrant = qdrant_service or QdrantService()
        self._embedding = embedding_service
        self._cache = embedding_cache
        self._stop = asyncio.Event()

    @property
    def config(self) -> ReconcilerConfig:
        """Expose the active config (used by tests and observability)."""
        return self._config

    def request_stop(self) -> None:
        """Signal the worker to exit after the current batch (graceful)."""
        self._stop.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.request_stop)
        except NotImplementedError:
            # asyncio cannot install loop signal handlers on Windows; the
            # KeyboardInterrupt fallback below still shuts the loop down.
            logger.warning("Loop signal handlers unavailable on this platform")

    async def run_forever(self) -> None:
        """Run the poll/process loop until a stop is requested (ARCHITECT §4.4)."""
        logger.info("Reconciler started")
        self._install_signal_handlers()
        try:
            while not self._stop.is_set():
                try:
                    processed = await self.run_once()
                except Exception:  # noqa: BLE001 — a DB blip must not kill the loop
                    logger.exception("Reconciler poll failed", extra={})
                    processed = 0
                if processed == 0:
                    await self._sleep_with_stop(self._config.poll_interval_seconds)
        finally:
            logger.info("Reconciler stopped")

    async def run_once(self) -> int:
        """Claim + process one batch; returns the number of rows handled."""
        batch = await self._claim_batch()
        if not batch:
            return 0

        semaphore = asyncio.Semaphore(self._config.parallelism)
        tasks = [asyncio.create_task(self._process_row(row, semaphore)) for row in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for row, result in zip(batch, results, strict=False):
            if isinstance(result, BaseException):
                logger.exception(
                    "Unhandled reconciler error while processing row",
                    extra={"outbox_id": row.id, "document_id": str(row.document_id)},
                )
        await self._refresh_dead_letter_count()
        return len(batch)

    async def _refresh_dead_letter_count(self) -> None:
        """Periodically refresh the dead_letter_count gauge (P-15)."""
        try:
            async with self._session_factory() as session:
                count = await outbox_queries.count_dead(session)
                metrics.dead_letter_count.set(count)
        except Exception:  # noqa: BLE001 — gauge refresh must not crash the loop
            logger.warning("Failed to refresh dead_letter_count gauge")

    async def _claim_batch(self) -> list[PendingOutboxRow]:
        """Fast-path existence check, then ``FOR UPDATE SKIP LOCKED`` claim."""
        async with self._session_factory() as session:
            metrics.index_lag_seconds.set(await outbox_queries.index_lag_seconds(session))
            outbox_pending_count = await outbox_queries.count_pending(session)
            metrics.outbox_pending_count.set(outbox_pending_count)
            metrics.reconciler_batch_size.set(self._config.batch_size)
            if not await outbox_queries.has_pending(session):
                return []
            batch = await outbox_queries.claim_pending(session, self._config.batch_size)
            if not batch:
                return []
            await outbox_queries.mark_in_progress_many(session, [row.id for row in batch])
            await session.commit()
            return batch

    async def _process_row(self, row: PendingOutboxRow, semaphore: asyncio.Semaphore) -> None:
        """Process one claimed row under a concurrency slot (semaphore 10)."""
        async with semaphore:
            async with self._session_factory() as session:
                await self._process_single(session, row)

    async def _process_single(self, session: AsyncSession, row: PendingOutboxRow) -> None:
        """Sync a single row to Qdrant; mark done/failed/dead (ARCHITECT §4.4)."""
        try:
            if row.op == "delete":
                await self._qdrant.delete_point(row.document_id)
            elif row.op == "upsert":
                await self._handle_upsert(session, row)
            else:
                raise ValueError(f"Unexpected outbox op: {row.op!r}")

            await outbox_queries.mark_done(session, row.id)
            await session.commit()
            logger.info(
                "Outbox row processed",
                extra={
                    "outbox_id": row.id,
                    "document_id": str(row.document_id),
                    "op": row.op,
                },
            )
        except Exception as exc:  # noqa: BLE001 — every failure becomes retry/dead
            await session.rollback()
            error_type = _classify_qdrant_error(exc)
            metrics.qdrant_upsert_errors_total.labels(error_type=error_type).inc()
            attempts_now = row.attempts + 1
            if attempts_now >= self._config.max_attempts:
                await outbox_queries.mark_dead(session, row.id, str(exc))
                metrics.dead_letters_total.inc()
                logger.error(
                    "Dead letter",
                    extra={
                        "outbox_id": row.id,
                        "document_id": str(row.document_id),
                        "op": row.op,
                        "attempts": attempts_now,
                        "error": str(exc),
                    },
                )
            else:
                backoff = self._compute_backoff(attempts_now)
                await outbox_queries.mark_failed(
                    session,
                    row.id,
                    str(exc),
                    datetime.now(UTC) + timedelta(seconds=backoff),
                )
                logger.warning(
                    "Outbox row failed; retry scheduled",
                    extra={
                        "outbox_id": row.id,
                        "document_id": str(row.document_id),
                        "op": row.op,
                        "attempts": attempts_now,
                        "next_retry_backoff_seconds": round(backoff, 2),
                        "error": str(exc),
                    },
                )
            await session.commit()

    async def _handle_upsert(self, session: AsyncSession, row: PendingOutboxRow) -> None:
        """Embed document.content (or reuse cache) and upsert to Qdrant.
        
        Supports dual-write when a promoted migration exists (PR-07).
        """
        document = await documents_queries.get_by_id(session, row.document_id)
        if document is None:
            raise ValueError(f"Document {row.document_id} not found for outbox row {row.id}")

        # Validate embedding model exists and is active
        await get_active_model_by_name(session, document.embedding_model)

        # Check for dual-write targets (promoted migration)
        dual_targets = get_dual_write_targets(session)

        if dual_targets:
            # Dual-write mode: compute vectors for each target and upsert to both collections
            await self._handle_dual_write(session, row, document, dual_targets)
        else:
            # Single-write mode (normal operation)
            await self._handle_single_write(session, row, document)

    async def _handle_single_write(
        self,
        session: AsyncSession,
        row: PendingOutboxRow,
        document: Document
    ) -> None:
        """Handle single upsert (normal operation)."""
        target_hash = row.content_hash or document.content_hash
        existing_hash = await self._qdrant.get_point_content_hash(row.document_id)
        if should_skip_upsert(target_hash, existing_hash):
            return  # fast-path: Qdrant already holds this exact content

        # Check if asymmetric indexing is enabled
        asymmetric_enabled = settings.feature_flags.asymmetric_indexing_enabled

        if asymmetric_enabled:
            # Use asymmetric indexing with chunking
            embedded = await self._get_embedding_service().embed_document(
                title=document.title,
                content=document.content,
                attributes=document.attributes or {}
            )

            # Build points for each chunk
            points = []
            for embedded_chunk in embedded.chunks:
                payload = QdrantPayload(
                    doc_id=document.id,
                    tenant_id=document.tenant_id,
                    language=document.language,
                    tags=document.tags,
                    attributes=document.attributes,
                    model_name=document.embedding_model,
                    model_rev=document.embedding_rev,
                    created_at=document.created_at,
                    content_hash=document.content_hash,
                    chunk_idx=embedded_chunk.chunk.chunk_idx,
                    section_type=embedded_chunk.chunk.section_type,
                ).model_dump(mode="json")

                point_id = self._qdrant.chunk_point_id(document.id, embedded_chunk.chunk.chunk_idx)
                points.append(qmodels.PointStruct(
                    id=point_id,
                    vector=embedded_chunk.vector.tolist(),
                    payload=payload,
                ))

            # Upsert points and prune stale chunks
            await self._qdrant.upsert_points(points)
            await self._qdrant.prune_chunks(document.id, keep=len(points))
        else:
            # Legacy single embedding (P-05)
            vector = await self._get_document_vector(document)
            payload = QdrantPayload(
                doc_id=document.id,
                tenant_id=document.tenant_id,
                language=document.language,
                tags=document.tags,
                attributes=document.attributes,
                model_name=document.embedding_model,
                model_rev=document.embedding_rev,
                created_at=document.created_at,
                content_hash=document.content_hash,
                chunk_idx=0,
                section_type=None,
            ).model_dump(mode="json")
            await self._qdrant.upsert_point(document.id, vector.tolist(), payload)
            # Prune any stale chunks from previous asymmetric indexing
            await self._qdrant.prune_chunks(document.id, keep=1)

    async def _handle_dual_write(
        self,
        session: AsyncSession,
        row: PendingOutboxRow,
        document: Document,
        dual_targets: list[tuple[str, str]]
    ) -> None:
        """Handle dual upsert (promoted migration mode).
        
        Computes vectors for each model and upserts to respective collections.
        """
        # Get document content
        content = document.content

        # Get or create embedding services for each target model
        embedding_services = {}
        for collection_name, model_name in dual_targets:
            if model_name not in embedding_services:
                # Create or get embedding service for this model
                service = self._get_embedding_service_for_model(model_name)
                embedding_services[model_name] = service

        # Compute vectors for each model
        vectors = {}
        for collection_name, model_name in dual_targets:
            service = embedding_services[model_name]
            # Check cache first
            cache = self._get_cache_for_model(model_name)
            target_hash = row.content_hash or document.content_hash
            existing_hash = await self._qdrant.get_point_content_hash(
                row.document_id,
                collection_name=collection_name
            )

            if should_skip_upsert(target_hash, existing_hash):
                # Use existing vector from Qdrant
                points = await self._qdrant.retrieve_points(
                    [row.document_id],
                    collection_name=collection_name,
                    with_vector=True
                )
                if points:
                    vectors[collection_name] = np.array(points[0].vector)
                else:
                    # Compute new vector
                    embedded_vectors = await service.embed_texts([content])
                    vectors[collection_name] = np.array(embedded_vectors[0])
            else:
                # Compute new vector
                embedded_vectors = await service.embed_texts([content])
                vectors[collection_name] = np.array(embedded_vectors[0])

        # Upsert to each target collection
        for collection_name, model_name in dual_targets:
            vector = vectors[collection_name]
            payload = QdrantPayload(
                doc_id=document.id,
                tenant_id=document.tenant_id,
                language=document.language,
                tags=document.tags,
                attributes=document.attributes,
                model_name=model_name,  # Use the model name for this collection
                model_rev=document.embedding_rev,
                created_at=document.created_at,
                content_hash=document.content_hash,
            ).model_dump(mode="json")
            await self._qdrant.upsert_point(
                document.id,
                vector.tolist(),
                payload,
                collection_name=collection_name
            )

    def _get_embedding_service_for_model(self, model_name: str) -> EmbeddingService:
        """Get or create embedding service for a specific model."""
        # Check if we already have a service for this model
        if not hasattr(self, '_embedding_services'):
            self._embedding_services = {}

        if model_name not in self._embedding_services:
            # Create new service for this model
            config = EmbeddingConfig(
                model_name=model_name,
                hf_model=settings.embedding.hf_model,  # TODO: Get model-specific HF ID
                expected_dimension=settings.embedding.expected_dimension,
                device=settings.embedding.device,
                cache=self._get_cache_for_model(model_name),
            )
            self._embedding_services[model_name] = EmbeddingService(config=config)

        return self._embedding_services[model_name]

    def _get_cache_for_model(self, model_name: str) -> EmbeddingCache:
        """Get or create cache for a specific model."""
        # Check if we already have a cache for this model
        if not hasattr(self, '_caches'):
            self._caches = {}

        if model_name not in self._caches:
            self._caches[model_name] = EmbeddingCache(get_redis_client())

        return self._caches[model_name]

    async def _get_document_vector(self, document: Document) -> np.ndarray:
        """Content-hash cache lookup → compute → store (ARCHITECT §4.3, §5.3)."""
        cache = self._get_cache()
        model_name = document.embedding_model
        content_hash = document.content_hash
        cached = await cache.get_by_content_hash(content_hash, model_name)
        if cached is not None:
            cached = np.asarray(cached, dtype=np.float32)
            return cached
        service = self._get_embedding_service()
        vectors = await service.embed_texts([document.content])
        vector = np.asarray(vectors[0], dtype=np.float32)
        await cache.set_by_content_hash(content_hash, model_name, vector)
        return vector

    def _get_embedding_service(self) -> EmbeddingService:
        if self._embedding is None:
            self._embedding = EmbeddingService(config=settings.embedding, cache=self._get_cache())
        return self._embedding

    def _get_cache(self) -> EmbeddingCache:
        if self._cache is None:
            self._cache = EmbeddingCache(get_redis_client())
        return self._cache

    def _compute_backoff(self, attempts: int) -> float:
        """Exponential backoff: min(2^attempts * base, max) seconds (§4.4)."""
        raw = self._config.base_backoff_seconds * (2**attempts)
        return float(min(raw, float(self._config.max_backoff_seconds)))

    async def _sleep_with_stop(self, seconds: float) -> None:
        """Sleep for ``seconds`` unless a stop was requested (interruptible)."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass


async def _main() -> None:
    worker = ReconcilerWorker()
    await worker.run_forever()


if __name__ == "__main__":
    from app.observability.logging import setup_logging

    setup_logging(level=settings.observability.log_level)
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass

__all__ = ["ReconcilerWorker"]
