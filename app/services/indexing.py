"""Indexing write-path service (ARCHITECT §2.4, §4.2, §4.3; P-07).

Implements the transactional outbox pattern: INSERT into ``documents`` +
INSERT into ``search_outbox`` atomically, then a best-effort XADD to the
``embeddings.queue`` Redis Stream. The ``content_hash`` fast-path (TRIZ-gate,
ROADMAP §3.3) skips re-enqueueing when identical content is re-indexed.
"""

import hashlib
import logging
import uuid

import redis.asyncio as aioredis
from langdetect import DetectorFactory, detect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IndexRequest, IndexResponse
from app.db.models import Document
from app.db.queries import documents, outbox
from app.db.queries.embedding_models import get_default_model

logger = logging.getLogger(__name__)

# langdetect profiles are non-deterministic without a fixed seed (0 <= seed <= 8).
DetectorFactory.seed = 0

EMBEDDINGS_QUEUE_STREAM = "embeddings.queue"
_DEFAULT_LANGUAGE = "en"
_EMBEDDING_REV = 1


class IndexingService:
    """Owns the ``POST /index`` transaction + Redis Streams hand-off."""

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client

    @staticmethod
    def content_hash(title: str, content: str) -> str:
        """sha256(title + "\\n" + content) — deterministic idempotency key."""
        return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()

    @staticmethod
    def detect_language(
        title: str, content: str, tenant_default: str | None = None
    ) -> str:
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

        await self._enqueue_upsert(doc_id)
        return IndexResponse(
            doc_id=doc_id,
            status="queued",
            indexed_at=created_at,
            wait_for_index_token=str(doc_id),
        )

    async def _enqueue_upsert(self, doc_id: uuid.UUID) -> None:
        """Publish to Redis Streams (best-effort; reconciler is the safety net)."""
        if self._redis is None:
            return
        try:
            await self._redis.xadd(
                EMBEDDINGS_QUEUE_STREAM,
                {"doc_id": str(doc_id), "op": "upsert"},
            )
        except Exception:  # noqa: BLE001 — Redis must not fail the request
            logger.warning(
                "Failed to enqueue to Redis Streams; reconciler will catch up",
                extra={"doc_id": str(doc_id)},
            )


__all__ = ["EMBEDDINGS_QUEUE_STREAM", "IndexingService"]
