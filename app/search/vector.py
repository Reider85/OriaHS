"""Vector search channel (P-10, ARCHITECT §6.1, §8.3).

The second half of hybrid search: query embedding (via P-06 cache → P-05
model) → Qdrant kNN with payload filters → top ``K_vector`` candidates that
P-11 feeds into RRF fusion.

``title``/``content`` are intentionally NOT stored in the Qdrant payload on
MVP (ARCHITECT §3.2, P-10 forbidden patterns); they are batch-loaded from
Postgres so a soft-deleted document can never surface through this channel.
"""

import asyncio
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import exceptions as qdrant_exceptions
from qdrant_client.http import models as qmodels
from qdrant_client.http.models import ScoredPoint
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.document import Document
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.filters import SearchFilters

SNIPPET_LENGTH = 200
SEARCH_TIMEOUT_SECONDS = 0.1  # 100 ms hard budget on Qdrant round-trip
COLLECTION_NAME = settings.qdrant.collection
DEFAULT_MODEL_NAME = settings.embedding.model_name


class VectorHit(BaseModel):
    """One candidate from the Qdrant vector channel."""

    doc_id: UUID
    score: float  # cosine similarity from Qdrant, normalized to [0, 1]
    title: str
    content_snippet: str  # leading 200 chars of ``content`` (no highlight)
    source: Literal["vector"] = "vector"


def build_qdrant_filter(
    tenant_id: UUID, filters: SearchFilters, model_name: str
) -> qmodels.Filter:
    """Convert ``SearchFilters`` + tenant isolation into a Qdrant ``Filter``.

    Mandatory ``must`` conditions (ARCHITECT §8.6, §5.7): tenant isolation
    and the active embedding model, so points written by a migrated model can
    never leak into the result set.
    """
    must: list[qmodels.Condition] = [
        qmodels.FieldCondition(
            key="tenant_id", match=qmodels.MatchValue(value=str(tenant_id))
        ),
        qmodels.FieldCondition(
            key="model_name", match=qmodels.MatchValue(value=model_name)
        ),
    ]

    if filters.language:
        must.append(
            qmodels.FieldCondition(
                key="language", match=qmodels.MatchAny(any=filters.language)
            )
        )
    if filters.tags_any:
        must.append(
            qmodels.FieldCondition(
                key="tags", match=qmodels.MatchAny(any=filters.tags_any)
            )
        )

    attrs = filters.attributes or {}
    category = attrs.get("category")
    if category is not None:
        must.append(
            qmodels.FieldCondition(
                key="attributes.category", match=qmodels.MatchValue(value=category)
            )
        )

    price = attrs.get("price")
    if isinstance(price, dict):
        must.append(
            qmodels.FieldCondition(
                key="attributes.price",
                range=qmodels.Range(gte=price.get("gte"), lte=price.get("lte")),
            )
        )

    if filters.created_after is not None:
        must.append(
            qmodels.FieldCondition(
                key="created_at",
                range=qmodels.DatetimeRange(gte=filters.created_after),
            )
        )

    return qmodels.Filter(must=must)


async def _load_titles(
    session: AsyncSession, doc_ids: list[UUID]
) -> dict[UUID, tuple[str, str]]:
    """Batch-load ``(title, snippet)`` for a set of doc_ids from Postgres.

    Only live documents are returned; soft-deleted rows are dropped so the
    vector channel respects ``deleted_at`` even though Qdrant still holds them.
    """
    if not doc_ids:
        return {}
    stmt = (
        select(
            Document.id,
            Document.title,
            func.left(Document.content, SNIPPET_LENGTH).label("content_snippet"),
        )
        .where(Document.id.in_(doc_ids), Document.deleted_at.is_(None))
    )
    rows = (await session.execute(stmt)).all()
    return {row.id: (row.title, row.content_snippet) for row in rows}


async def vector_search(
    session: AsyncSession,
    qdrant_client: AsyncQdrantClient,
    embedding_service: EmbeddingService,
    embedding_cache: EmbeddingCache,
    query: str,
    tenant_id: UUID,
    filters: SearchFilters,
    model_name: str | None = None,
    k: int = 50,
) -> list[VectorHit]:
    """Run the vector channel: query embed → Qdrant kNN + payload filter.

    Raises:
        QdrantTimeoutError: Qdrant did not answer within 100 ms.
        QdrantUnavailableError: Qdrant is unreachable / returned an error.
    """
    model_name = model_name or DEFAULT_MODEL_NAME

    vec = await embedding_cache.get_query_embedding(query, model_name)
    if vec is None:
        vec = await embedding_service.embed_query(query)
        await embedding_cache.set_query_embedding(query, model_name, vec)

    qfilter = build_qdrant_filter(tenant_id, filters, model_name)

    try:
        scored = await asyncio.wait_for(
            qdrant_client.search(
                collection_name=COLLECTION_NAME,
                query_vector=vec.tolist(),
                query_filter=qfilter,
                limit=k,
                with_payload=True,
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise QdrantTimeoutError(
            f"Qdrant search exceeded {SEARCH_TIMEOUT_SECONDS * 1000:.0f} ms"
        ) from exc
    except qdrant_exceptions.ApiException as exc:
        status = getattr(exc, "status_code", None)
        raise QdrantUnavailableError(
            f"Qdrant unavailable (status={status}): {exc}"
        ) from exc
    except Exception as exc:  # aiohttp connection errors etc.
        raise QdrantUnavailableError(f"Qdrant unavailable: {exc}") from exc

    if not scored:
        return []

    pairs: list[tuple[UUID, ScoredPoint]] = []
    for point in scored:
        payload = point.payload or {}
        raw_doc_id = payload.get("doc_id")
        if raw_doc_id is None:
            continue
        doc_id = raw_doc_id if isinstance(raw_doc_id, UUID) else UUID(str(raw_doc_id))
        pairs.append((doc_id, point))

    if not pairs:
        return []

    titles = await _load_titles(session, [doc_id for doc_id, _ in pairs])

    hits: list[VectorHit] = []
    for doc_id, point in pairs:
        title, snippet = titles.get(doc_id, ("", ""))
        raw_score = point.score
        hits.append(
            VectorHit(
                doc_id=doc_id,
                score=(raw_score + 1) / 2,  # cosine [-1, 1] → [0, 1]
                title=title,
                content_snippet=snippet,
            )
        )
    return hits
