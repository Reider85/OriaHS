"""``POST /index`` + ``DELETE /index/{doc_id}`` write-path routes.

:201 Created (POST): fresh/updated index; the fast-path no-op (identical
content re-indexed) also returns 201 but with ``IndexResponse.status ==
"no_change"`` so clients can distinguish it.

:204 No Content (DELETE): soft-delete dual-write to ``documents`` +
``search_outbox`` (ARCHITECT §14.4, P-08). Repeating the DELETE of an
already-deleted document is idempotent and stays 204; a never-existing
document is 404; an unparseable UUID is 422.
"""

from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IndexRequest, IndexResponse, IndexStatusResponse
from app.db.queries import outbox
from app.db.redis_client import get_redis_client
from app.db.session import get_session
from app.services.indexing import IndexingService

router = APIRouter(prefix="/index", tags=["index"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis_client)]


@router.post("", response_model=IndexResponse, status_code=status.HTTP_201_CREATED)
async def index_document(
    req: IndexRequest,
    session: SessionDep,
    redis_client: RedisDep,
) -> IndexResponse:
    """Index a document: dual-write to ``documents`` + ``search_outbox``."""
    service = IndexingService(redis_client=redis_client)
    return await service.create(session, req)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: UUID,
    session: SessionDep,
    redis_client: RedisDep,
) -> None:
    """Soft-delete a document: dual-write to ``documents`` + ``outbox(delete)``."""
    service = IndexingService(redis_client=redis_client)
    await service.soft_delete(session, doc_id)


@router.get("/status/{token}", response_model=IndexStatusResponse)
async def index_status(
    token: str,
    session: SessionDep,
) -> IndexStatusResponse:
    """Polling for read-your-writes: client checks if a document is searchable."""
    # Validate UUID format (FastAPI won't auto-validate str path param)
    try:
        doc_id = UUID(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid token format"
        )

    status_val = await outbox.get_status_by_document_id(session, doc_id)
    if status_val is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )

    # Normalize: in_progress and pending are both "pending" for the client
    if status_val in ("pending", "in_progress", "failed"):
        normalized = "pending"
    elif status_val == "done":
        normalized = "done"
    elif status_val == "dead":
        normalized = "dead"
    else:
        normalized = "pending"

    response = IndexStatusResponse(token=token, status=normalized)
    return response
