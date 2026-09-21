"""P-10: vector search channel — Qdrant kNN + payload filter (ARCHITECT §6.1).

Unit tests with a mocked async Qdrant client: asserts filter construction
(§8.3), tenant isolation, query-embedding cache behaviour, result mapping,
score normalization and timeout propagation.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from qdrant_client.http import models as qmodels

from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.filters import SearchFilters
from app.search.vector import (
    DEFAULT_MODEL_NAME,
    SNIPPET_LENGTH,
    VectorHit,
    build_qdrant_filter,
    vector_search,
)


def _scored_point(doc_id: str, score: float, tenant_id: str) -> qmodels.ScoredPoint:
    return qmodels.ScoredPoint(
        id=doc_id,
        version=1,
        score=score,
        payload={
            "doc_id": doc_id,
            "tenant_id": tenant_id,
            "model_name": DEFAULT_MODEL_NAME,
        },
    )


def _fake_session(title_map: dict[str, dict]) -> AsyncMock:
    """Session mock whose ``execute`` returns title/snippet rows for doc_ids."""
    rows = []
    for doc_id, values in title_map.items():
        row = MagicMock()
        row.id = uuid.UUID(doc_id)
        row.title = values["title"]
        row.content_snippet = values["snippet"]
        rows.append(row)
    result = MagicMock()
    result.all.return_value = rows

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def _hit_vector() -> np.ndarray:
    return np.full(1024, 0.01, dtype=np.float32)


def test_build_qdrant_filter_tenant_isolation() -> None:
    tenant = uuid.uuid4()
    qfilter = build_qdrant_filter(tenant, SearchFilters(), DEFAULT_MODEL_NAME)

    assert isinstance(qfilter.must, list)
    keys = {c.key for c in qfilter.must if isinstance(c, qmodels.FieldCondition)}
    assert "tenant_id" in keys
    assert "model_name" in keys
    tenant_cond = next(
        c
        for c in qfilter.must
        if isinstance(c, qmodels.FieldCondition) and c.key == "tenant_id"
    )
    assert tenant_cond.match == qmodels.MatchValue(value=str(tenant))


def test_build_qdrant_filter_language() -> None:
    tenant = uuid.uuid4()
    qfilter = build_qdrant_filter(
        tenant, SearchFilters(language=["ru"]), DEFAULT_MODEL_NAME
    )
    cond = next(
        c
        for c in qfilter.must
        if isinstance(c, qmodels.FieldCondition) and c.key == "language"
    )
    assert cond.match == qmodels.MatchAny(any=["ru"])


def test_build_qdrant_filter_tags() -> None:
    tenant = uuid.uuid4()
    qfilter = build_qdrant_filter(
        tenant, SearchFilters(tags_any=["news"]), DEFAULT_MODEL_NAME
    )
    cond = next(
        c
        for c in qfilter.must
        if isinstance(c, qmodels.FieldCondition) and c.key == "tags"
    )
    assert cond.match == qmodels.MatchAny(any=["news"])


def test_build_qdrant_filter_attributes() -> None:
    tenant = uuid.uuid4()
    qfilter = build_qdrant_filter(
        tenant,
        SearchFilters(attributes={"category": "AI", "price": {"gte": 100, "lte": 5000}}),
        DEFAULT_MODEL_NAME,
    )
    conds = [c for c in qfilter.must if isinstance(c, qmodels.FieldCondition)]
    category = next(c for c in conds if c.key == "attributes.category")
    assert category.match == qmodels.MatchValue(value="AI")

    price = next(c for c in conds if c.key == "attributes.price")
    assert price.range == qmodels.Range(gte=100, lte=5000)


def test_build_qdrant_filter_created_after() -> None:
    tenant = uuid.uuid4()
    since = datetime.now(UTC)
    qfilter = build_qdrant_filter(
        tenant, SearchFilters(created_after=since), DEFAULT_MODEL_NAME
    )
    cond = next(
        c
        for c in qfilter.must
        if isinstance(c, qmodels.FieldCondition) and c.key == "created_at"
    )
    assert cond.range == qmodels.DatetimeRange(gte=since)


def test_build_qdrant_filter_no_optional_conditions() -> None:
    tenant = uuid.uuid4()
    qfilter = build_qdrant_filter(tenant, SearchFilters(), DEFAULT_MODEL_NAME)
    assert len(qfilter.must) == 2  # only tenant_id + model_name


async def test_returns_vector_hits_with_titles() -> None:
    tenant = uuid.uuid4()
    doc_id = str(uuid.uuid4())
    client = MagicMock()
    client.search = AsyncMock(
        return_value=[_scored_point(doc_id, 0.9, str(tenant))]
    )

    cache = MagicMock(spec=EmbeddingCache)
    cache.get_query_embedding = AsyncMock(return_value=await _hit_vector())

    service = MagicMock(spec=EmbeddingService)
    session = _fake_session(
        {doc_id: {"title": "Matched", "snippet": "leading content..."}}
    )

    hits = await vector_search(
        session=session,
        qdrant_client=client,
        embedding_service=service,
        embedding_cache=cache,
        query="test query",
        tenant_id=tenant,
        filters=SearchFilters(),
        k=50,
    )

    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, VectorHit)
    assert hit.source == "vector"
    assert hit.doc_id == uuid.UUID(doc_id)
    assert hit.title == "Matched"
    assert hit.content_snippet == "leading content..."
    assert 0 <= hit.score <= 1
    service.embed_query.assert_not_awaited()
    cache.set_query_embedding.assert_not_awaited()


async def test_results_sorted_by_score_desc_and_k_forwarded() -> None:
    tenant = uuid.uuid4()
    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    client = MagicMock()
    client.search = AsyncMock(
        return_value=[
            _scored_point(doc_a, 0.9, str(tenant)),
            _scored_point(doc_b, 0.7, str(tenant)),
        ]
    )
    cache = MagicMock(spec=EmbeddingCache)
    cache.get_query_embedding = AsyncMock(return_value=await _hit_vector())
    service = MagicMock(spec=EmbeddingService)

    hits = await vector_search(
        session=_fake_session({}),
        qdrant_client=client,
        embedding_service=service,
        embedding_cache=cache,
        query="q",
        tenant_id=tenant,
        filters=SearchFilters(),
        k=5,
    )

    assert hits == sorted(hits, key=lambda h: -h.score)
    call = client.search.await_args
    assert call.kwargs["limit"] == 5


async def test_query_embedding_cache_miss_calls_embed_and_stores() -> None:
    tenant = uuid.uuid4()
    doc_id = str(uuid.uuid4())
    client = MagicMock()
    client.search = AsyncMock(return_value=[_scored_point(doc_id, 0.8, str(tenant))])

    cache = MagicMock(spec=EmbeddingCache)
    cache.get_query_embedding = AsyncMock(return_value=None)
    cache.set_query_embedding = AsyncMock()

    service = MagicMock(spec=EmbeddingService)
    service.embed_query = AsyncMock(return_value=await _hit_vector())

    await vector_search(
        session=_fake_session({}),
        qdrant_client=client,
        embedding_service=service,
        embedding_cache=cache,
        query="unknown query",
        tenant_id=tenant,
        filters=SearchFilters(),
    )

    service.embed_query.assert_awaited_once_with("unknown query")
    cache.set_query_embedding.assert_awaited_once_with(
        "unknown query", DEFAULT_MODEL_NAME, service.embed_query.return_value
    )


async def test_qdrant_timeout_raises_qdrant_timeout() -> None:
    tenant = uuid.uuid4()
    client = MagicMock()
    client.search = AsyncMock(side_effect=TimeoutError())
    cache = MagicMock(spec=EmbeddingCache)
    cache.get_query_embedding = AsyncMock(return_value=await _hit_vector())
    service = MagicMock(spec=EmbeddingService)

    with pytest.raises(QdrantTimeoutError):
        await vector_search(
            session=_fake_session({}),
            qdrant_client=client,
            embedding_service=service,
            embedding_cache=cache,
            query="q",
            tenant_id=tenant,
            filters=SearchFilters(),
        )


async def test_qdrant_api_exception_raises_unavailable() -> None:
    from qdrant_client.http.exceptions import ApiException

    tenant = uuid.uuid4()
    client = MagicMock()
    client.search = AsyncMock(side_effect=ApiException("connection refused"))
    cache = MagicMock(spec=EmbeddingCache)
    cache.get_query_embedding = AsyncMock(return_value=await _hit_vector())
    service = MagicMock(spec=EmbeddingService)

    with pytest.raises(QdrantUnavailableError):
        await vector_search(
            session=_fake_session({}),
            qdrant_client=client,
            embedding_service=service,
            embedding_cache=cache,
            query="q",
            tenant_id=tenant,
            filters=SearchFilters(),
        )


async def test_empty_result_returns_empty_list() -> None:
    tenant = uuid.uuid4()
    client = MagicMock()
    client.search = AsyncMock(return_value=[])
    cache = MagicMock(spec=EmbeddingCache)
    cache.get_query_embedding = AsyncMock(return_value=await _hit_vector())
    service = MagicMock(spec=EmbeddingService)

    hits = await vector_search(
        session=_fake_session({}),
        qdrant_client=client,
        embedding_service=service,
        embedding_cache=cache,
        query="no match",
        tenant_id=tenant,
        filters=SearchFilters(),
    )
    assert hits == []


def test_snippet_length_constant() -> None:
    assert SNIPPET_LENGTH == 200
