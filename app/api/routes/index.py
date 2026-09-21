"""``POST /index`` write-path route (ARCHITECT §14.2, §2.4; P-07).

201 Created for a fresh/updated index; the fast-path no-op (identical content
re-indexed) also returns 201 but with ``IndexResponse.status == "no_change"``
so clients can distinguish it. The ``DELETE`` verbs arrive in P-08.
"""

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IndexRequest, IndexResponse
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
