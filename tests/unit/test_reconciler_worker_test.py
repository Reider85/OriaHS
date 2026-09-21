"""P-13: reconciler worker — unit tests (ARCHITECT §4.4, ROADMAP §3.2.5).

Pure-mock tests for the outbox sync loop: claim/process orchestration,
upsert + delete handling, the ``content_hash`` fast-path, exponential
backoff, the ``dead`` safety net and graceful-stop behaviour. No Docker.
"""

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock
from uuid import UUID, uuid4

import numpy as np
import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ReconcilerConfig
from app.db.models import Document
from app.db.queries import outbox as outbox_mod
from app.db.queries.outbox import PendingOutboxRow
from app.observability import metrics
from app.reconciler.worker import ReconcilerWorker


class _SessionCtx:
    """Async context manager around a shared (mock) session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> bool:
        return False


def _build_worker(
    *,
    session: AsyncSession | None = None,
    qdrant: object | None = None,
    embedding: object | None = None,
    cache: object | None = None,
    config: ReconcilerConfig | None = None,
) -> tuple[ReconcilerWorker, AsyncSession]:
    """Construct a worker wired to mock collaborators."""
    if session is None:
        session = AsyncMock(spec=AsyncSession)
    worker = ReconcilerWorker(
        config=config or ReconcilerConfig(),
        session_factory=lambda: _SessionCtx(session),
        qdrant_service=qdrant,  # type: ignore[arg-type]
        embedding_service=embedding,  # type: ignore[arg-type]
        embedding_cache=cache,  # type: ignore[arg-type]
    )
    return worker, session


def _document(doc_id: UUID | None = None, *, content_hash: str = "doc-hash") -> Document:
    return Document(
        id=doc_id or uuid4(),
        tenant_id=uuid4(),
        external_ref="ref-1",
        title="Hello",
        content="Hello world",
        language="en",
        tags=["greeting"],
        attributes={"category": "test"},
        embedding_model="bge-m3-v1",
        embedding_rev=1,
        content_hash=content_hash,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _row(
    *, op: str = "upsert", attempts: int = 0, content_hash: str = "doc-hash"
) -> PendingOutboxRow:
    return PendingOutboxRow(
        id=1,
        document_id=uuid4(),
        op=op,
        attempts=attempts,
        content_hash=content_hash,
    )


def _vector(batch: int = 1) -> np.ndarray:
    return np.full((batch, 1024), 0.01, dtype=np.float32)


# --- claim / orchestration -------------------------------------------------


async def test_run_once_returns_zero_when_outbox_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, _ = _build_worker()
    monkeypatch.setattr(outbox_mod, "index_lag_seconds", AsyncMock(return_value=0.0))
    monkeypatch.setattr(outbox_mod, "has_pending", AsyncMock(return_value=False))
    claimed = AsyncMock(side_effect=AssertionError("claim must not run on empty outbox"))
    monkeypatch.setattr(outbox_mod, "claim_pending", claimed)

    processed = await worker.run_once()

    assert processed == 0
    claimed.assert_not_awaited()


async def test_run_once_claims_and_processes_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, _ = _build_worker()
    row = _row(content_hash="abc")
    monkeypatch.setattr(outbox_mod, "index_lag_seconds", AsyncMock(return_value=3.0))
    monkeypatch.setattr(outbox_mod, "has_pending", AsyncMock(return_value=True))
    monkeypatch.setattr(outbox_mod, "claim_pending", AsyncMock(return_value=[row]))
    mark_progress = AsyncMock()
    monkeypatch.setattr(outbox_mod, "mark_in_progress_many", mark_progress)
    worker._process_row = AsyncMock()  # type: ignore[method-assign]

    processed = await worker.run_once()

    assert processed == 1
    mark_progress.assert_awaited_once_with(ANY, [row.id])
    worker._process_row.assert_awaited()
    assert worker._process_row.await_args is not None
    assert worker._process_row.await_args.args[0] == row
    assert metrics.index_lag_seconds._value.get() == 3.0


# --- upsert path -----------------------------------------------------------


async def test_process_single_upsert_embeds_and_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _document(content_hash="doc-hash")
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=doc)
    qdrant = AsyncMock()
    qdrant.get_point_content_hash.return_value = None
    cache = AsyncMock()
    cache.get_by_content_hash.return_value = None
    embedding = AsyncMock()
    embedding.embed_texts.return_value = _vector()
    worker, _ = _build_worker(
        session=session, qdrant=qdrant, embedding=embedding, cache=cache
    )
    mark_done = AsyncMock()
    monkeypatch.setattr(outbox_mod, "mark_done", mark_done)
    row = _row(content_hash="doc-hash")

    await worker._process_single(session, row)

    qdrant.upsert_point.assert_awaited_once()
    assert qdrant.upsert_point.await_args is not None
    payload = qdrant.upsert_point.await_args.args[2]
    assert payload["doc_id"] == str(doc.id)
    assert payload["content_hash"] == "doc-hash"
    assert payload["model_name"] == "bge-m3-v1"
    cache.set_by_content_hash.assert_awaited_once()
    mark_done.assert_awaited_once_with(session, row.id)
    session.commit.assert_awaited_once()


async def test_process_single_reuses_cached_embedding() -> None:
    doc = _document(content_hash="doc-hash")
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=doc)
    qdrant = AsyncMock()
    qdrant.get_point_content_hash.return_value = None
    cache = AsyncMock()
    cache.get_by_content_hash.return_value = _vector(1)[0]
    embedding = AsyncMock()
    embedding.embed_texts.side_effect = AssertionError("model must not run on cache hit")
    worker, _ = _build_worker(
        session=session, qdrant=qdrant, embedding=embedding, cache=cache
    )

    await worker._process_single(session, _row(content_hash="doc-hash"))

    embedding.embed_texts.assert_not_awaited()
    cache.set_by_content_hash.assert_not_awaited()


async def test_fastpath_skips_upsert_when_hash_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCHITECT §4.3: identical content_hash in Qdrant → no upsert, still done."""
    doc = _document(content_hash="same")
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=doc)
    qdrant = AsyncMock()
    qdrant.get_point_content_hash.return_value = "same"
    embedding = AsyncMock()
    embedding.embed_texts.side_effect = AssertionError("no embedding on fast-path")
    worker, _ = _build_worker(
        session=session, qdrant=qdrant, embedding=embedding, cache=AsyncMock()
    )
    mark_done = AsyncMock()
    monkeypatch.setattr(outbox_mod, "mark_done", mark_done)

    await worker._process_single(session, _row(content_hash="same"))

    qdrant.upsert_point.assert_not_awaited()
    embedding.embed_texts.assert_not_awaited()
    mark_done.assert_awaited_once_with(session, 1)


async def test_fastpath_does_not_skip_when_hashes_differ() -> None:
    """ARCHITECT §4.3: different content_hash in Qdrant → re-embed + upsert."""
    doc = _document(content_hash="new-hash")
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=doc)
    qdrant = AsyncMock()
    qdrant.get_point_content_hash.return_value = "old-hash"
    cache = AsyncMock()
    cache.get_by_content_hash.return_value = None
    embedding = AsyncMock()
    embedding.embed_texts.return_value = _vector()
    worker, _ = _build_worker(
        session=session, qdrant=qdrant, embedding=embedding, cache=cache
    )

    await worker._process_single(session, _row(content_hash="new-hash"))

    qdrant.upsert_point.assert_awaited_once()


# --- delete path -----------------------------------------------------------


async def test_process_single_delete_removes_point(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock(spec=AsyncSession)
    qdrant = AsyncMock()
    worker, _ = _build_worker(session=session, qdrant=qdrant)
    mark_done = AsyncMock()
    monkeypatch.setattr(outbox_mod, "mark_done", mark_done)
    row = _row(op="delete")

    await worker._process_single(session, row)

    qdrant.delete_point.assert_awaited_once_with(row.document_id)
    mark_done.assert_awaited_once_with(session, row.id)


# --- failure + backoff + dead ----------------------------------------------


async def test_failure_marks_failed_with_exponential_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _document(content_hash="doc-hash")
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=doc)
    qdrant = AsyncMock()
    qdrant.get_point_content_hash.return_value = None
    qdrant.upsert_point.side_effect = ConnectionError("qdrant is down")
    cache = AsyncMock()
    cache.get_by_content_hash.return_value = _vector(1)[0]
    worker, _ = _build_worker(
        session=session,
        qdrant=qdrant,
        embedding=AsyncMock(),
        cache=cache,
        config=ReconcilerConfig(base_backoff_seconds=10, max_backoff_seconds=3600),
    )
    mark_failed = AsyncMock()
    monkeypatch.setattr(outbox_mod, "mark_failed", mark_failed)
    row = _row(attempts=2)  # 3rd attempt → backoff = min(2^3 * 10s, 1h) = 80 s

    await worker._process_single(session, row)

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args is not None
    _, _, error, next_retry = mark_failed.await_args.args
    assert "qdrant is down" in str(error)
    delta = (next_retry - datetime.now(UTC)).total_seconds()
    assert 70 < delta <= 80
    session.commit.assert_awaited_once()


async def test_max_attempts_marks_dead_and_increments_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 20 failed attempts a row goes ``dead`` (TRIZ-gate, ROADMAP §3.3)."""
    from qdrant_client.http.exceptions import ApiException

    doc = _document(content_hash="doc-hash")
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=doc)
    qdrant = AsyncMock()
    qdrant.get_point_content_hash.side_effect = ApiException("qdrant unavailable")
    worker, _ = _build_worker(
        session=session,
        qdrant=qdrant,
        embedding=AsyncMock(),
        cache=AsyncMock(),
        config=ReconcilerConfig(max_attempts=20),
    )
    mark_dead = AsyncMock()
    monkeypatch.setattr(outbox_mod, "mark_dead", mark_dead)
    before = REGISTRY.get_sample_value("dead_letters_total") or 0.0
    row = _row(attempts=19)  # 20th attempt → dead

    await worker._process_single(session, row)

    mark_dead.assert_awaited_once_with(session, row.id, ANY)
    after = REGISTRY.get_sample_value("dead_letters_total") or 0.0
    assert after == before + 1


# --- config + lifecycle ----------------------------------------------------


def test_compute_backoff_caps_at_max() -> None:
    worker, _ = _build_worker(
        config=ReconcilerConfig(base_backoff_seconds=10, max_backoff_seconds=3600)
    )
    assert worker._compute_backoff(1) == 20.0
    assert worker._compute_backoff(6) == 640.0
    assert worker._compute_backoff(20) == 3600.0


async def test_sleep_interrupted_by_request_stop() -> None:
    worker, _ = _build_worker()
    worker.request_stop()
    t0 = time.monotonic()
    await worker._sleep_with_stop(30)
    assert time.monotonic() - t0 < 5


async def test_run_forever_exits_on_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock(spec=AsyncSession)
    worker = ReconcilerWorker(
        config=ReconcilerConfig(poll_interval_seconds=30),
        session_factory=lambda: _SessionCtx(session),
        qdrant_service=None,
    )
    monkeypatch.setattr(
        outbox_mod, "has_pending", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        outbox_mod, "index_lag_seconds", AsyncMock(return_value=0.0)
    )

    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=5)


def test_config_surface_exposed() -> None:
    config = ReconcilerConfig()
    worker, _ = _build_worker(config=config)
    assert worker.config is config
    assert config.parallelism == 10


async def test_missing_document_raises_value_error() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=None)
    qdrant = AsyncMock()
    worker, _ = _build_worker(session=session, qdrant=qdrant)

    with pytest.raises(ValueError, match="not found"):
        await worker._handle_upsert(session, _row())
