"""Query helpers for the ``search_outbox`` table (ARCHITECT §3.1, §4.4).

P-02 created the table; P-07/P-08 write rows (upsert/delete) and P-13's
reconciler claims + completes them. All reconciler queries implement the
ARCHITECT §4.4 algorithm: ``FOR UPDATE SKIP LOCKED`` claiming, exponential
backoff and the ``dead`` safety net (TRIZ-gate, ROADMAP §3.3).
"""

from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OutboxItem

_PENDING_FILTER = "status IN ('pending','failed') AND next_retry_at <= now()"


class PendingOutboxRow(NamedTuple):
    """One claimed ``search_outbox`` row (subset of columns, ARCHITECT §4.4)."""

    id: int
    document_id: UUID
    op: str
    attempts: int
    content_hash: str | None


async def add_upsert(
    session: AsyncSession, document_id: UUID, content_hash: str | None
) -> None:
    """Enqueue an ``upsert`` outbox row for a document (inside a transaction)."""
    session.add(
        OutboxItem(
            document_id=document_id,
            op="upsert",
            content_hash=content_hash,
        )
    )


async def add_delete(session: AsyncSession, document_id: UUID) -> None:
    """Enqueue a ``delete`` outbox row (P-08, idempotent per document)."""
    session.add(
        OutboxItem(
            document_id=document_id,
            op="delete",
            content_hash=None,
        )
    )


async def has_pending(session: AsyncSession) -> bool:
    """Cheap existence check before the expensive claim (ARCHITECT §4.4 opt.).

    The reconciler calls this each poll; only when it returns True does it
    pay for the ``FOR UPDATE SKIP LOCKED`` scan. Idempotent, no locks.
    """
    result = await session.execute(
        text(
            f"SELECT EXISTS (SELECT 1 FROM search_outbox WHERE {_PENDING_FILTER})"
        )
    )
    exists = result.scalar_one()
    assert isinstance(exists, bool)
    return exists


async def claim_pending(
    session: AsyncSession, batch_size: int
) -> list[PendingOutboxRow]:
    """Claim at most ``batch_size`` rows for processing (ARCHITECT §4.4).

    ``FOR UPDATE SKIP LOCKED`` safely parallelizes multiple reconciler
    workers: a row locked by another worker is skipped instead of waited on.
    The caller owns the transaction and must commit before processing.
    """
    result = await session.execute(
        text(
            "SELECT id, document_id, op, attempts, content_hash "
            "FROM search_outbox "
            f"WHERE {_PENDING_FILTER} "
            "ORDER BY next_retry_at "
            "LIMIT :batch_size "
            "FOR UPDATE SKIP LOCKED"
        ),
        {"batch_size": batch_size},
    )
    return [
        PendingOutboxRow(
            id=row.id,
            document_id=row.document_id,
            op=row.op,
            attempts=row.attempts,
            content_hash=row.content_hash,
        )
        for row in result.all()
    ]


async def mark_in_progress_many(session: AsyncSession, ids: list[int]) -> None:
    """Bump ``attempts`` and flip status to ``in_progress`` for claimed rows."""
    if not ids:
        return
    await session.execute(
        text(
            "UPDATE search_outbox "
            "SET status = 'in_progress', attempts = attempts + 1, updated_at = now() "
            "WHERE id = ANY(CAST(:ids AS bigint[]))"
        ),
        {"ids": ids},
    )


async def mark_done(session: AsyncSession, outbox_id: int) -> None:
    """Mark one row as successfully synchronized to Qdrant."""
    await session.execute(
        text(
            "UPDATE search_outbox "
            "SET status = 'done', last_error = NULL, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": outbox_id},
    )


async def mark_failed(
    session: AsyncSession,
    outbox_id: int,
    error: str,
    next_retry_at: datetime,
) -> None:
    """Set ``failed`` with an exponential-backoff retry time (ARCHITECT §4.4)."""
    await session.execute(
        text(
            "UPDATE search_outbox "
            "SET status = 'failed', last_error = :error, "
            "    next_retry_at = :next_retry_at, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": outbox_id, "error": error[:4096], "next_retry_at": next_retry_at},
    )


async def mark_dead(session: AsyncSession, outbox_id: int, error: str) -> None:
    """Move a permanently failing row to ``dead`` (TRIZ-gate, ROADMAP §3.3).

    ``dead`` rows no longer participate in ``has_pending``/``claim_pending``
    (they are excluded by the status filter), so they cannot clog the queue.
    """
    await session.execute(
        text(
            "UPDATE search_outbox "
            "SET status = 'dead', last_error = :error, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": outbox_id, "error": error[:4096]},
    )


async def index_lag_seconds(session: AsyncSession) -> float:
    """Age of the oldest pending/failed row (0 when the outbox is empty).

    Powers the ``index_lag_seconds`` Prometheus gauge (ARCHITECT §11.1).
    """
    result = await session.execute(
        text(
            "SELECT EXTRACT(EPOCH FROM now() - min(created_at)) "
            "FROM search_outbox "
            "WHERE status IN ('pending','failed')"
        )
    )
    lag = result.scalar_one()
    if lag is None:
        return 0.0
    return float(lag)


__all__ = [
    "PendingOutboxRow",
    "add_upsert",
    "add_delete",
    "has_pending",
    "claim_pending",
    "mark_in_progress_many",
    "mark_done",
    "mark_failed",
    "mark_dead",
    "index_lag_seconds",
]
