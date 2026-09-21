"""P-11: SearchOrchestrator unit tests (ARCHITECT §6.1, §7.1).

Tests the orchestrator with mocked lexical and vector channels.
No real infrastructure — mocks for session, qdrant, embedding service/cache.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.api.schemas import SearchRequest, SearchResponse
from app.config import SearchConfig
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.lexical import LexicalHit
from app.search.orchestrator import SearchOrchestrator
from app.search.vector import VectorHit


def _make_request(**overrides) -> SearchRequest:
    defaults = {
        "query": "test query",
        "tenant_id": uuid4(),
        "top_k": 20,
        "timeout_ms": 500,
    }
    defaults.update(overrides)
    return SearchRequest(**defaults)


def _lex_hit(doc_id: UUID | None = None, score: float = 1.0) -> LexicalHit:
    return LexicalHit(
        doc_id=doc_id or uuid4(),
        score=score,
        title="title",
        content_snippet="snippet",
    )


def _vec_hit(doc_id: UUID | None = None, score: float = 0.9) -> VectorHit:
    return VectorHit(
        doc_id=doc_id or uuid4(),
        score=score,
        title="title",
        content_snippet="snippet",
    )


@pytest.fixture
def config() -> SearchConfig:
    return SearchConfig()


@pytest.fixture
def mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_qdrant() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_emb_svc() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_emb_cache() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def orchestrator(mock_session, mock_qdrant, mock_emb_svc, mock_emb_cache, config):
    return SearchOrchestrator(
        session=mock_session,
        qdrant_client=mock_qdrant,
        embedding_service=mock_emb_svc,
        embedding_cache=mock_emb_cache,
        config=config,
    )


class TestSearchOrchestrator:
    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_both_channels_return_results(
        self, mock_lex, mock_vec, orchestrator, config
    ):
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        mock_lex.return_value = [_lex_hit(d1), _lex_hit(d2, score=0.8)]
        mock_vec.return_value = [_vec_hit(d1, score=0.95), _vec_hit(d3, score=0.7)]

        req = _make_request()
        resp = await orchestrator.search(req)

        assert isinstance(resp, SearchResponse)
        assert resp.total_lexical == 2
        assert resp.total_vector == 2
        assert not resp.degraded
        # d1 is in both channels → top result
        assert resp.hits[0].doc_id == d1
        assert len(resp.hits) <= req.top_k

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_qdrant_timeout_degraded_mode(
        self, mock_lex, mock_vec, orchestrator
    ):
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.side_effect = QdrantTimeoutError("timeout")

        req = _make_request()
        resp = await orchestrator.search(req)

        assert resp.degraded is True
        assert resp.total_lexical == 1
        assert resp.total_vector == 0
        assert len(resp.hits) == 1
        assert resp.hits[0].doc_id == d1

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_qdrant_unavailable_degraded_mode(
        self, mock_lex, mock_vec, orchestrator
    ):
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.side_effect = QdrantUnavailableError("unavailable")

        req = _make_request()
        resp = await orchestrator.search(req)

        assert resp.degraded is True
        assert resp.total_vector == 0

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_both_channels_fail_returns_degraded_empty(
        self, mock_lex, mock_vec, orchestrator
    ):
        mock_lex.side_effect = Exception("PG down")
        mock_vec.side_effect = QdrantUnavailableError("qdrant down")

        req = _make_request()
        resp = await orchestrator.search(req)

        assert resp.degraded is True
        assert resp.hits == []
        assert resp.total_lexical == 0
        assert resp.total_vector == 0

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_empty_results(self, mock_lex, mock_vec, orchestrator):
        mock_lex.return_value = []
        mock_vec.return_value = []

        req = _make_request()
        resp = await orchestrator.search(req)

        assert resp.hits == []
        assert resp.total_lexical == 0
        assert resp.total_vector == 0
        assert not resp.degraded

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_explain_mode_adds_debug_info(
        self, mock_lex, mock_vec, orchestrator
    ):
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1, score=1.5)]
        mock_vec.return_value = [_vec_hit(d1, score=0.9)]

        req = _make_request(explain=True)
        resp = await orchestrator.search(req)

        assert len(resp.hits) == 1
        debug = resp.hits[0].debug
        assert debug is not None
        assert "rrf_score" in debug
        assert debug["lexical_rank"] == 1
        assert debug["vector_rank"] == 1
        assert debug["lexical_score"] == 1.5
        assert debug["vector_score"] == 0.9

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_latency_ms_in_response(
        self, mock_lex, mock_vec, orchestrator
    ):
        mock_lex.return_value = []
        mock_vec.return_value = []

        req = _make_request()
        resp = await orchestrator.search(req)

        assert resp.latency_ms >= 0
        assert resp.latency_ms < 5000  # generous upper bound

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_top_k_limits_results(self, mock_lex, mock_vec, orchestrator):
        hits = [_lex_hit(uuid4(), score=float(10 - i)) for i in range(10)]
        mock_lex.return_value = hits
        mock_vec.return_value = []

        req = _make_request(top_k=3)
        resp = await orchestrator.search(req)

        assert len(resp.hits) == 3

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_dedup_same_doc_in_both_channels(
        self, mock_lex, mock_vec, orchestrator
    ):
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]

        req = _make_request()
        resp = await orchestrator.search(req)

        # d1 should appear once, with RRF-summed score
        doc_ids = [h.doc_id for h in resp.hits]
        assert doc_ids.count(d1) == 1
