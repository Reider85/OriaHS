"""Query helpers for the ``documents`` table (ARCHITECT §3.1, P-07/P-08).

Only the write-path helpers needed by ``IndexingService`` live here for now;
lexical search (P-09) and vector search (P-10) will add their own read
helpers. ``updated_at`` is maintained by the ``trg_documents_updated_at``
trigger (P-01), not by these statements.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document


async def get_by_tenant_external_ref(
    session: AsyncSession, tenant_id: UUID, external_ref: str
) -> Document | None:
    """Look up a live document by its unique (tenant_id, external_ref) key."""
    result = await session.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.external_ref == external_ref,
            Document.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, doc_id: UUID) -> Document | None:
    """Fetch a single document by primary key (regardless of deleted status)."""
    return await session.get(Document, doc_id)


async def add(session: AsyncSession, document: Document) -> None:
    """Stage a new document for INSERT (the caller owns the transaction)."""
    session.add(document)


async def update_content(
    session: AsyncSession,
    doc_id: UUID,
    *,
    title: str,
    content: str,
    language: str,
    tags: list[str],
    attributes: dict[str, Any],
    content_hash: str,
) -> None:
    """Overwrite mutable document fields on re-index (fast-path miss)."""
    statement = (
        update(Document)
        .where(Document.id == doc_id)
        .values(
            title=title,
            content=content,
            language=language,
            tags=tags,
            attributes=attributes,
            content_hash=content_hash,
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(statement)


async def soft_delete(session: AsyncSession, doc_id: UUID) -> int:
    """Soft-delete a document, returning the number of affected rows (P-08).

    The ``WHERE deleted_at IS NULL`` guard keeps the operation idempotent.
    """
    statement = (
        update(Document)
        .where(Document.id == doc_id, Document.deleted_at.is_(None))
        .values(deleted_at=func.now())
        .execution_options(synchronize_session=False)
    )
    result = cast(CursorResult[Any], await session.execute(statement))
    return result.rowcount or 0
