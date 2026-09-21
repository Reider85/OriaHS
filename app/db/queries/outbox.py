"""Query helpers for the ``search_outbox`` table (ARCHITECT §3.1, §4.4).

P-02 created the table; P-07 writes rows (upsert), P-13's reconciler will
claim/complete them. Only the write helper needed by ``IndexingService``
lives here for now.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OutboxItem


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
