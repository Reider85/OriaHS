"""P-13: reconciler worker — integration tests (ARCHITECT §4.4 DoD §3.4).

Drives the whole outbox loop against disposable PG + Qdrant + Redis
containers. Embedding is stubbed (loading BAAI/bge-m3 is far too slow for
CI); the cache is the real Redis-backed one. Covers the upsert/delete sync,
the ``content_hash`` fast-path, the failed→backoff path and the ``dead``
state (TRIZ-gate) after max_attempts.
"""

import hashlib
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import numpy as np
import pytest
import redis.asyncio as aioredis
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.qdrant import QdrantContainer

from app.config import ReconcilerConfig
from app.embedding.cache import EmbeddingCache
from app.reconciler.worker import ReconcilerWorker
from app.search.qdrant_payload import QdrantPayload
from app.services.qdrant import QdrantService

COLLECTION = "documents"
DIMENSION = 1024
_INGEST_SQL = text(
    "INSERT INTO documents "
    "(id, tenant_id, external_ref, title, content, language, embedding_model, content_hash) "
    "VALUES (:id, :tenant_id, :external_ref, :title, :content, 'en', 'bge-m3-v1', :ch)"
)
_OUTBOX_SQL = text(
    "INSERT INTO search_outbox (document_id, op, status, attempts, next_retry_at, content_hash) "
    "VALUES (:doc, :op, 'pending', :attempts, now(), :ch)"
)
# Qdrant normalizes vectors for cosine distance, so use distinct *unit* axes
# to keep the stubbed embedding and the pre-existing point distinguishable.
_EMBED_VECTOR = [1.0, *([0.0] * (DIMENSION - 1))]
_PREEXISTING_VECTOR = [0.0, 1.0, *([0.0] * (DIMENSION - 2))]


class StubEmbedder:
    """Deterministic 1024-dim embedder — replaces bge-m3 in tests."""

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        vector = np.array(_EMBED_VECTOR, dtype=np.float32)
        return np.tile(vector, (len(texts), 1))


@pytest.fixture(scope="session")
def qdrant_client_conn() -> Iterator[AsyncQdrantClient]:
    """Disposable Qdrant (same image as docker-compose)."""
    with QdrantContainer() as container:
        yield container.get_async_client()


@pytest.fixture
async def qdrant_service(
    qdrant_client_conn: AsyncQdrantClient,
) -> AsyncIterator[QdrantService]:
    """``QdrantService`` bound to a fresh ``documents`` collection per test."""
    await qdrant_client_conn.delete_collection(COLLECTION, timeout=30)
    await qdrant_client_conn.create_collection(
        collection_name=COLLECTION,
        vectors_config=qmodels.VectorParams(
            size=DIMENSION, distance=qmodels.Distance.COSINE
        ),
        timeout=30,
    )
    service = QdrantService(client=qdrant_client_conn, collection_name=COLLECTION)
    yield service


def _make_session_factory(engine: AsyncEngine) -> Callable[[], AsyncSession]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return lambda: factory()


def _make_worker(
    engine: AsyncEngine,
    *,
    qdrant: QdrantService,
    redis: aioredis.Redis,
    config: ReconcilerConfig | None = None,
) -> ReconcilerWorker:
    """Worker with a real DB session factory, real cache, stubbed embedder."""
    return ReconcilerWorker(
        config=config or ReconcilerConfig(),
        session_factory=_make_session_factory(engine),
        qdrant_service=qdrant,
        embedding_service=StubEmbedder(),  # type: ignore[arg-type]
        embedding_cache=EmbeddingCache(redis),
    )


async def _seed_upsert(
    engine: AsyncEngine,
    doc_id: UUID,
    tenant_id: UUID,
    content_hash: str,
    *,
    attempts: int = 0,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            _INGEST_SQL,
            {
                "id": doc_id,
                "tenant_id": tenant_id,
                "external_ref": f"ref-{doc_id}",
                "title": "Title",
                "content": "Body",
                "ch": content_hash,
            },
        )
        await conn.execute(
            _OUTBOX_SQL,
            {"doc": doc_id, "op": "upsert", "attempts": attempts, "ch": content_hash},
        )


async def _seed_delete(
    engine: AsyncEngine, doc_id: UUID, tenant_id: UUID, content_hash: str
) -> None:
    """Enqueue a ``delete`` row; the FK still needs the document to exist."""
    async with engine.begin() as conn:
        await conn.execute(
            _INGEST_SQL,
            {
                "id": doc_id,
                "tenant_id": tenant_id,
                "external_ref": f"ref-{doc_id}",
                "title": "Title",
                "content": "Body",
                "ch": content_hash,
            },
        )
        await conn.execute(
            _OUTBOX_SQL,
            {"doc": doc_id, "op": "delete", "attempts": 0, "ch": None},
        )


async def _outbox_status(engine: AsyncEngine, doc_id: UUID) -> tuple[str, int]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, attempts FROM search_outbox "
                    "WHERE document_id = :doc ORDER BY id DESC LIMIT 1"
                ),
                {"doc": doc_id},
            )
        ).first()
    assert row is not None, "expected an outbox row"
    return row[0], int(row[1])


async def _retrieve_points(
    qdrant_client_conn: AsyncQdrantClient, doc_id: UUID
) -> list[qmodels.Record]:
    return await qdrant_client_conn.retrieve(
        collection_name=COLLECTION,
        ids=[str(doc_id)],
        with_payload=True,
        with_vectors=True,
    )


def _content_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


@pytest.mark.slow  # PG + Qdrant + Redis testcontainers
async def test_worker_syncs_upsert_to_qdrant(
    engine: AsyncEngine, async_redis: aioredis.Redis, qdrant_service: QdrantService,
    qdrant_client_conn: AsyncQdrantClient,
) -> None:
    doc_id = uuid4()
    tenant_id = uuid4()
    ch = _content_hash("Title", "Body")
    await _seed_upsert(engine, doc_id, tenant_id, ch)

    worker = _make_worker(engine, qdrant=qdrant_service, redis=async_redis)
    processed = await worker.run_once()

    assert processed == 1
    status, _ = await _outbox_status(engine, doc_id)
    assert status == "done"

    points = await _retrieve_points(qdrant_client_conn, doc_id)
    assert len(points) == 1
    payload = points[0].payload
    assert payload is not None
    assert payload["doc_id"] == str(doc_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["content_hash"] == ch
    assert payload["model_name"] == "bge-m3-v1"
    vector = cast(
        "list[float]", points[0].vector
    )  # dense vector stored here (test data)
    assert len(vector) == DIMENSION
    assert np.allclose(vector, _EMBED_VECTOR)


@pytest.mark.slow
async def test_worker_delete_removes_point(
    engine: AsyncEngine,
    async_redis: aioredis.Redis,
    qdrant_service: QdrantService,
    qdrant_client_conn: AsyncQdrantClient,
) -> None:
    doc_id = uuid4()
    tenant_id = uuid4()
    ch = _content_hash("Title", "Body")
    await qdrant_service.upsert_point(
        doc_id,
        _PREEXISTING_VECTOR,
        QdrantPayload(
            doc_id=doc_id,
            tenant_id=tenant_id,
            language="en",
            tags=[],
            attributes={},
            model_name="bge-m3-v1",
            model_rev=1,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            content_hash=ch,
        ).model_dump(mode="json"),
    )
    await _seed_delete(engine, doc_id, tenant_id, ch)

    worker = _make_worker(engine, qdrant=qdrant_service, redis=async_redis)
    processed = await worker.run_once()

    assert processed == 1
    status, _ = await _outbox_status(engine, doc_id)
    assert status == "done"
    assert await _retrieve_points(qdrant_client_conn, doc_id) == []


@pytest.mark.slow
async def test_fastpath_skips_upsert_when_content_hash_matches(
    engine: AsyncEngine,
    async_redis: aioredis.Redis,
    qdrant_service: QdrantService,
    qdrant_client_conn: AsyncQdrantClient,
) -> None:
    """ARCHITECT §4.3/DoD §3.4: identical hash in Qdrant → no re-embed, done."""
    doc_id = uuid4()
    tenant_id = uuid4()
    ch = _content_hash("Title", "Body")
    await qdrant_service.upsert_point(
        doc_id,
        _PREEXISTING_VECTOR,  # a different (pre-existing) direction
        QdrantPayload(
            doc_id=doc_id,
            tenant_id=tenant_id,
            language="en",
            tags=[],
            attributes={},
            model_name="bge-m3-v1",
            model_rev=1,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            content_hash=ch,
        ).model_dump(mode="json"),
    )
    await _seed_upsert(engine, doc_id, tenant_id, ch)

    worker = _make_worker(engine, qdrant=qdrant_service, redis=async_redis)
    processed = await worker.run_once()

    assert processed == 1
    status, _ = await _outbox_status(engine, doc_id)
    assert status == "done"
    points = await _retrieve_points(qdrant_client_conn, doc_id)
    vector = cast("list[float]", points[0].vector)
    assert np.allclose(vector, _PREEXISTING_VECTOR)  # untouched → fast-path hit


@pytest.mark.slow
async def test_qdrant_failure_marks_failed_with_backoff(
    engine: AsyncEngine,
    async_redis: aioredis.Redis,
) -> None:
    doc_id = uuid4()
    tenant_id = uuid4()
    ch = _content_hash("Title", "Body")
    await _seed_upsert(engine, doc_id, tenant_id, ch, attempts=2)

    dead_client = AsyncQdrantClient(url="http://127.0.0.1:1")
    service = QdrantService(client=dead_client, collection_name=COLLECTION)
    worker = _make_worker(
        engine,
        qdrant=service,
        redis=async_redis,
        config=ReconcilerConfig(base_backoff_seconds=10, max_backoff_seconds=3600),
    )
    try:
        assert await worker.run_once() == 1
    finally:
        await dead_client.close()

    status, attempts = await _outbox_status(engine, doc_id)
    assert status == "failed"
    assert attempts == 3
    async with engine.connect() as conn:
        next_retry = await conn.scalar(
            text(
                "SELECT next_retry_at FROM search_outbox "
                "WHERE document_id = :doc ORDER BY id DESC LIMIT 1"
            ),
            {"doc": doc_id},
        )
    assert next_retry is not None


@pytest.mark.slow
async def test_qdrant_failure_marks_dead_after_max_attempts(
    engine: AsyncEngine, async_redis: aioredis.Redis,
) -> None:
    """TRIZ-gate: after 20 failed attempts the row goes ``dead`` (not retried)."""
    from prometheus_client import REGISTRY

    doc_id = uuid4()
    tenant_id = uuid4()
    ch = _content_hash("Title", "Body")
    await _seed_upsert(
        engine, doc_id, tenant_id, ch, attempts=ReconcilerConfig().max_attempts - 1
    )

    dead_client = AsyncQdrantClient(url="http://127.0.0.1:1")
    service = QdrantService(client=dead_client, collection_name=COLLECTION)
    before = REGISTRY.get_sample_value("dead_letters_total") or 0.0
    worker = _make_worker(engine, qdrant=service, redis=async_redis)
    try:
        assert await worker.run_once() == 1
    finally:
        await dead_client.close()

    status, _ = await _outbox_status(engine, doc_id)
    assert status == "dead"
    after = REGISTRY.get_sample_value("dead_letters_total") or 0.0
    assert after == before + 1


@pytest.mark.slow
async def test_in_progress_rows_are_not_claimed(
    engine: AsyncEngine, async_redis: aioredis.Redis, qdrant_service: QdrantService,
) -> None:
    """``in_progress`` rows stay out of the pending scan (ARCHITECT §4.4)."""
    doc_id = uuid4()
    tenant_id = uuid4()
    ch = _content_hash("Title", "Body")
    async with engine.begin() as conn:
        await conn.execute(
            _INGEST_SQL,
            {
                "id": doc_id,
                "tenant_id": tenant_id,
                "external_ref": f"ref-{doc_id}",
                "title": "Title",
                "content": "Body",
                "ch": ch,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO search_outbox (document_id, op, status, attempts, next_retry_at, content_hash) "
                "VALUES (:doc, 'upsert', 'in_progress', 1, now(), :ch)"
            ),
            {"doc": doc_id, "ch": ch},
        )

    worker = _make_worker(engine, qdrant=qdrant_service, redis=async_redis)
    assert await worker.run_once() == 0
