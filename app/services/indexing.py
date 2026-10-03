"""Indexing write-path service (ARCHITECT §2.4, §4.2, §4.3; P-07, P-08).

Implements the transactional outbox pattern: INSERT into ``documents`` +
INSERT into ``search_outbox`` atomically, then a best-effort XADD to the
``embeddings.queue`` Redis Stream. The ``content_hash`` fast-path (TRIZ-gate,
ROADMAP §3.3) skips re-enqueueing when identical content is re-indexed.
P-08 adds the idempotent ``soft_delete`` path with ``op='delete'``.
"""

import hashlib
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from langdetect import DetectorFactory, detect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IndexRequest, IndexResponse
from app.config import settings
from app.db.models import Document
from app.db.queries import documents, outbox
from app.db.queries.embedding_models import get_default_model
from app.observability.logging import get_logger, tenant_id_ctx
from app.services.throttle import OutboxThrottle

logger = get_logger(__name__)

# langdetect profiles are non-deterministic without a fixed seed (0 <= seed <= 8).
DetectorFactory.seed = 0

EMBEDDINGS_QUEUE_STREAM = "embeddings.queue"
_DEFAULT_LANGUAGE = "en"
_EMBEDDING_REV = 1


class IndexingService:
    """Owns the ``POST /index`` transaction + Redis Streams hand-off."""

    def __init__(
        self, redis_client: aioredis.Redis | None = None, throttle: OutboxThrottle | None = None
    ) -> None:
        self._redis = redis_client
        self._throttle = throttle

    @staticmethod
    def content_hash(title: str, content: str) -> str:
        """sha256(title + "\\n" + content) — deterministic idempotency key."""
        return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()

    @staticmethod
    def detect_language(title: str, content: str, tenant_default: str | None = None) -> str:
        """ISO 639-1 language tag; tenant default (else "en") is the fallback."""
        sample = f"{title} {content}".strip()
        if sample:
            try:
                return str(detect(sample))
            except Exception:  # noqa: BLE001 — langdetect may raise on short input
                logger.exception("Language detection failed, using fallback")
        return tenant_default or _DEFAULT_LANGUAGE

    async def create(self, session: AsyncSession, req: IndexRequest) -> IndexResponse:
        """Index (or fast-path skip) a document.

        The whole dual-write runs in one transaction; the Redis XADD happens
        strictly after commit so a rollback cannot leave an orphan task.
        """
        token = tenant_id_ctx.set(str(req.tenant_id))
        try:
            content_hash = self.content_hash(req.title, req.content)
            language = req.language or self.detect_language(req.title, req.content)

            existing = await documents.get_by_tenant_external_ref(
                session, req.tenant_id, req.external_ref
            )

            if existing is not None and existing.content_hash == content_hash:
                return IndexResponse(
                    doc_id=existing.id,
                    status="no_change",
                    indexed_at=existing.created_at,
                    wait_for_index_token=str(existing.id),
                )

            if existing is not None:
                await documents.update_content(
                    session,
                    existing.id,
                    title=req.title,
                    content=req.content,
                    language=language,
                    tags=req.tags,
                    attributes=req.attributes,
                    content_hash=content_hash,
                )
                doc_id = existing.id
                created_at = existing.created_at
            else:
                model = await get_default_model(session)
                document = Document(
                    tenant_id=req.tenant_id,
                    external_ref=req.external_ref,
                    title=req.title,
                    content=req.content,
                    language=language,
                    tags=req.tags,
                    attributes=req.attributes,
                    embedding_model=model.name,
                    embedding_rev=_EMBEDDING_REV,
                    content_hash=content_hash,
                )
                await documents.add(session, document)
                await session.flush()  # materialise the server-default document UUID
                doc_id = document.id
                created_at = document.created_at

            await outbox.add_upsert(session, doc_id, content_hash)
            await session.commit()

            # Check throttle status (C-11)
            throttled = False
            status_str = "queued"

            if self._throttle is not None and settings.feature_flags.throttle_enabled:
                if await self._throttle.should_throttle():
                    throttled = True
                    status_str = "throttled"
                    # Check if reindex should be triggered
                    await self._throttle.check_and_log_reindex_trigger()
                else:
                    status_str = "queued"

            await self._enqueue("upsert", doc_id)
            return IndexResponse(
                doc_id=doc_id,
                status=status_str,
                indexed_at=created_at,
                wait_for_index_token=str(doc_id),
                throttled=throttled,
            )
        finally:
            tenant_id_ctx.reset(token)

    async def soft_delete(self, session: AsyncSession, doc_id: UUID) -> bool:
        """Soft-delete a document and enqueue a ``delete`` sync (P-08).

        Returns ``True`` when the document was actually soft-deleted and a new
        outbox task was created, ``False`` when it was already deleted
        (idempotent no-op, no new outbox row). Raises ``HTTPException`` 404
        when no such document exists.
        """
        rowcount = await documents.soft_delete(session, doc_id)
        if rowcount == 0:
            existing = await documents.get_by_id(session, doc_id)
            if existing is None or existing.deleted_at is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found",
                )
            return False

        await outbox.add_delete(session, doc_id)
        await session.commit()

        await self._enqueue("delete", doc_id)
        return True

    async def _enqueue(self, op: str, doc_id: UUID) -> None:
        """Publish to Redis Streams (best-effort; reconciler is the safety net)."""
        if self._redis is None:
            return
        try:
            await self._redis.xadd(
                EMBEDDINGS_QUEUE_STREAM,
                {"doc_id": str(doc_id), "op": op},
            )
        except Exception:  # noqa: BLE001 — Redis must not fail the request
            logger.warning(
                "Failed to enqueue to Redis Streams; reconciler will catch up",
                extra={"doc_id": str(doc_id), "op": op},
            )


__all__ = ["EMBEDDINGS_QUEUE_STREAM", "IndexingService"]
