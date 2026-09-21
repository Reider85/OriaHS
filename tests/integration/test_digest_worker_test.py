"""P-14: digest worker — integration tests (ARCHITECT §4.4, ROADMAP §3.2.5/§3.3).

Seeds ``dead`` outbox rows directly (the reconciler marks them ``dead`` after
max_attempts — that path is covered in the P-13 suite) and verifies the
hourly digest aggregates them by ``(tenant_id, model_name, error_type)`` into
``search_outbox_dead_digest`` with bounded sample arrays. The digest table is
created from the 004 migration DDL (no ORM model on MVP — raw SQL only).
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import ReconcilerConfig
from app.reconciler.digest import DigestWorker

_DD_STATEMENTS = [
    (
        "CREATE TABLE search_outbox_dead_digest ("
        " id BIGSERIAL PRIMARY KEY,"
        " tenant_id UUID NOT NULL,"
        " model_name TEXT NOT NULL,"
        " error_type TEXT NOT NULL,"
        " count BIGINT NOT NULL,"
        " first_seen_at TIMESTAMPTZ NOT NULL,"
        " last_seen_at TIMESTAMPTZ NOT NULL,"
        " sample_doc_ids UUID[] NOT NULL DEFAULT '{}',"
        " sample_errors TEXT[] NOT NULL DEFAULT '{}',"
        " UNIQUE (tenant_id, model_name, error_type)"
        ")"
    ),
    ("CREATE INDEX search_outbox_dead_digest_tenant_idx ON search_outbox_dead_digest (tenant_id)"),
    (
        "CREATE INDEX search_outbox_dead_digest_last_seen_idx "
        "ON search_outbox_dead_digest (last_seen_at DESC)"
    ),
]

_DOC_SQL = text(
    "INSERT INTO documents "
    "(tenant_id, external_ref, title, content, language, embedding_model, content_hash) "
    "VALUES (:tenant_id, :external_ref, :title, :content, 'en', :model, :content_hash) "
    "RETURNING id"
)
_DEAD_SQL = text(
    "INSERT INTO search_outbox (document_id, op, status, attempts, last_error, updated_at) "
    "VALUES (:doc, 'upsert', 'dead', 20, :error, :updated_at)"
)


@pytest.fixture
async def digest_table(engine: AsyncEngine) -> AsyncIterator[None]:
    """Create ``search_outbox_dead_digest`` (004 DDL; no ORM model on MVP)."""
    async with engine.begin() as conn:
        for stmt in _DD_STATEMENTS:
            await conn.execute(text(stmt))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE search_outbox_dead_digest"))


def _make_session_factory(engine: AsyncEngine) -> Callable[[], AsyncSession]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return lambda: factory()


def _make_worker(engine: AsyncEngine, *, config: ReconcilerConfig | None = None) -> DigestWorker:
    return DigestWorker(
        config=config or ReconcilerConfig(),
        session_factory=_make_session_factory(engine),
    )


async def _seed_dead(
    engine: AsyncEngine,
    *,
    tenant_id: UUID,
    model: str,
    error: str,
    updated_at: datetime | None = None,
) -> UUID:
    """Insert a document + a ``dead`` outbox row; returns the document id.

    ``updated_at`` pins when the row went ``dead`` — the sole cursor used by
    the digest aggregation (``updated_at > since``).
    """
    async with engine.begin() as conn:
        doc_id = (
            await conn.execute(
                _DOC_SQL,
                {
                    "tenant_id": tenant_id,
                    "external_ref": f"ref-{uuid4()}",
                    "title": "Title",
                    "content": "Body",
                    "model": model,
                    "content_hash": hashlib.sha256(b"Title\nBody").hexdigest(),
                },
            )
        ).scalar_one()
        await conn.execute(
            _DEAD_SQL,
            {
                "doc": doc_id,
                "error": error,
                "updated_at": updated_at or datetime.now(UTC),
            },
        )
    return doc_id


async def _fetch_digest(
    engine: AsyncEngine, tenant_id: UUID, model_name: str, error_type: str
) -> dict | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT tenant_id, model_name, error_type, count, "
                    "first_seen_at, last_seen_at, sample_doc_ids, sample_errors "
                    "FROM search_outbox_dead_digest "
                    "WHERE tenant_id = :t AND model_name = :m AND error_type = :e"
                ),
                {"t": tenant_id, "m": model_name, "e": error_type},
            )
        ).first()
    if row is None:
        return None
    return {
        "tenant_id": row[0],
        "model_name": row[1],
        "error_type": row[2],
        "count": int(row[3]),
        "first_seen_at": row[4],
        "last_seen_at": row[5],
        "sample_doc_ids": row[6],
        "sample_errors": row[7],
    }


async def _digest_row_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(text("SELECT count(*) FROM search_outbox_dead_digest"))
            ).scalar_one()
        )


@pytest.mark.slow  # PG testcontainer
async def test_single_dead_row_aggregated(engine: AsyncEngine, digest_table: None) -> None:
    tenant_id = uuid4()
    doc_id = await _seed_dead(
        engine,
        tenant_id=tenant_id,
        model="bge-m3-v1",
        error="EmbeddingService: unknown model 'nonexistent-model'",
    )

    worker = _make_worker(engine)
    assert await worker.run_once() == 1

    row = await _fetch_digest(engine, tenant_id, "bge-m3-v1", "embedding_failed")
    assert row is not None
    assert row["count"] == 1
    assert doc_id in row["sample_doc_ids"]
    assert "nonexistent-model" in row["sample_errors"][0]


@pytest.mark.slow
async def test_error_type_classification(engine: AsyncEngine, digest_table: None) -> None:
    """ILIKE buckets: qdrant/timeout, embedding/model, payload/validation, other."""
    tenant_id = uuid4()
    buckets = {
        "qdrant_unavailable": "QdrantUnavailableException: connection refused",
        "qdrant_unavailable_timeout": "TimeoutError: qdrant did not answer",
        "embedding_failed": "EmbeddingService: failed to embed document",
        "payload_invalid": "PayloadValidationError: extra field 'x' not permitted",
        "other": "random infrastructure blowup",
    }
    for error in buckets.values():
        await _seed_dead(engine, tenant_id=tenant_id, model="bge-m3-v1", error=error)

    assert await _make_worker(engine).run_once() == 4

    assert (await _fetch_digest(engine, tenant_id, "bge-m3-v1", "qdrant_unavailable")) is not None
    assert (await _fetch_digest(engine, tenant_id, "bge-m3-v1", "embedding_failed")) is not None
    assert (await _fetch_digest(engine, tenant_id, "bge-m3-v1", "payload_invalid")) is not None
    assert (await _fetch_digest(engine, tenant_id, "bge-m3-v1", "other")) is not None

    grouped = await _fetch_digest(engine, tenant_id, "bge-m3-v1", "qdrant_unavailable")
    assert grouped is not None
    assert grouped["count"] == 2  # the timeout error lands in the same bucket


@pytest.mark.slow
async def test_repeat_run_increments_same_group(engine: AsyncEngine, digest_table: None) -> None:
    """New dead row in the same (tenant, model, error) group → count += 1."""
    tenant_id = uuid4()

    # Pin ``updated_at`` so each digest run sees a deterministic, disjoint
    # window (the production default is a rolling 2h lookback; here we slice
    # at the row timestamps to exercise the incremental contract precisely).
    t0 = datetime.now(UTC) - timedelta(hours=3)
    doc_a = await _seed_dead(
        engine,
        tenant_id=tenant_id,
        model="bge-m3-v1",
        error="EmbeddingService: model load failed",
        updated_at=t0,
    )
    worker = _make_worker(engine)
    assert await worker.run_once(since=t0 - timedelta(minutes=1)) == 1

    first = await _fetch_digest(engine, tenant_id, "bge-m3-v1", "embedding_failed")
    assert first is not None
    assert first["count"] == 1
    assert doc_a in first["sample_doc_ids"]

    t1 = t0 + timedelta(minutes=30)
    doc_b = await _seed_dead(
        engine,
        tenant_id=tenant_id,
        model="bge-m3-v1",
        error="EmbeddingService: model load failed",
        updated_at=t1,
    )
    # Window starts after doc_a went dead → only doc_b qualifies this run.
    assert await worker.run_once(since=t0 + timedelta(minutes=1)) == 1

    second = await _fetch_digest(engine, tenant_id, "bge-m3-v1", "embedding_failed")
    assert second is not None
    assert second["count"] == 2
    assert second["last_seen_at"] == t1
    assert doc_a in second["sample_doc_ids"]
    assert doc_b in second["sample_doc_ids"]


@pytest.mark.slow
async def test_run_without_new_dead_rows_is_noop(engine: AsyncEngine, digest_table: None) -> None:
    """Digest is idempotent: no dead rows in the window → data unchanged."""
    tenant_id = uuid4()
    doc_id = await _seed_dead(
        engine,
        tenant_id=tenant_id,
        model="bge-m3-v1",
        error="EmbeddingService: failed",
    )
    worker = _make_worker(engine)
    assert await worker.run_once() == 1
    before = await _fetch_digest(engine, tenant_id, "bge-m3-v1", "embedding_failed")
    assert before is not None
    assert doc_id in before["sample_doc_ids"]

    # A run with zero qualifying rows (all dead rows older than ``since``)
    # must not touch the digest table.
    future = datetime.now(UTC) + timedelta(hours=1)
    assert await worker.run_once(since=future) == 0
    assert await _digest_row_count(engine) == 1

    after = await _fetch_digest(engine, tenant_id, "bge-m3-v1", "embedding_failed")
    assert after is not None
    assert after == before


@pytest.mark.slow
async def test_run_forever_graceful_stop(engine: AsyncEngine, digest_table: None) -> None:
    """request_stop() exits the hourly loop after the current aggregation."""
    worker = _make_worker(engine, config=ReconcilerConfig(digest_interval_minutes=60))
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.2)  # let the startup aggregation + sleep(3600s) start
    worker.request_stop()
    await asyncio.wait_for(task, timeout=5)
    assert task.done()
    assert not task.exception()
