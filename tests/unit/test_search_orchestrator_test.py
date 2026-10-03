"""P-11 / C-05: SearchOrchestrator unit tests (ARCHITECT §6.1, §7.1).

Tests the orchestrator with mocked lexical and vector channels, plus the
Critical-phase rerank paths (C-05: rerank, circuit breaker, speculative).
No real infrastructure — mocks for session, qdrant, embedding service/cache.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.api.schemas import SearchRequest, SearchResponse
from app.config import (
    CircuitBreakerConfig,
    FeatureFlags,
    RerankerConfig,
    SearchConfig,
)
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.exceptions import RerankerUnavailableException
from app.reranker.schemas import RerankCandidate, RerankResult
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.lexical import LexicalHit
from app.search.orchestrator import SearchOrchestrator
from app.search.speculative import SpeculativeReranker
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
    async def test_both_channels_return_results(self, mock_lex, mock_vec, orchestrator, config):
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
    async def test_qdrant_timeout_degraded_mode(self, mock_lex, mock_vec, orchestrator):
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
    async def test_qdrant_unavailable_degraded_mode(self, mock_lex, mock_vec, orchestrator):
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
    async def test_explain_mode_adds_debug_info(self, mock_lex, mock_vec, orchestrator):
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
    async def test_latency_ms_in_response(self, mock_lex, mock_vec, orchestrator):
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
    async def test_dedup_same_doc_in_both_channels(self, mock_lex, mock_vec, orchestrator):
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]

        req = _make_request()
        resp = await orchestrator.search(req)

        # d1 should appear once, with RRF-summed score
        doc_ids = [h.doc_id for h in resp.hits]
        assert doc_ids.count(d1) == 1

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_weighted_fusion_dispatch(self, mock_lex, mock_vec, orchestrator):
        """Test that fusion='weighted' calls weighted_fuse, not rrf_fuse."""
        d1, d2 = uuid4(), uuid4()
        mock_lex.return_value = [_lex_hit(d1, score=1.5), _lex_hit(d2, score=1.0)]
        mock_vec.return_value = [_vec_hit(d1, score=0.9), _vec_hit(d2, score=0.8)]

        req = _make_request(fusion="weighted", fusion_alpha=0.7)
        resp = await orchestrator.search(req)

        assert isinstance(resp, SearchResponse)
        assert resp.total_lexical == 2
        assert resp.total_vector == 2
        # Should have weighted fusion results (not RRF)
        assert len(resp.hits) <= req.top_k

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_explain_with_weighted_fusion(self, mock_lex, mock_vec, orchestrator):
        """Test explain mode includes weighted fusion debug info."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1, score=1.5)]
        mock_vec.return_value = [_vec_hit(d1, score=0.9)]

        req = _make_request(fusion="weighted", fusion_alpha=0.7, explain=True)
        resp = await orchestrator.search(req)

        assert len(resp.hits) == 1
        debug = resp.hits[0].debug
        assert debug is not None
        assert debug["fusion_strategy"] == "weighted"
        assert debug["fusion_alpha"] == 0.7
        assert debug["weighted_score"] is not None
        assert debug["rrf_score"] is None  # Should be None for weighted fusion


# ---------------------------------------------------------------------------
# C-05: rerank integration (ARCHITECT §6.1, §6.5, §6.6)
# ---------------------------------------------------------------------------


class MockRerankerService:
    """Async cross-encoder stub that records calls and returns fixed scores."""

    def __init__(self, scores: dict[UUID, float] | None = None, fail: bool = False) -> None:
        self._scores = scores or {}
        self._fail = fail
        self.calls: list[tuple[str, list[UUID]]] = []

    async def rerank(
        self, query: str, docs: list[RerankCandidate], top_k: int | None = None
    ) -> list[RerankResult]:
        self.calls.append((query, [d.doc_id for d in docs]))
        if self._fail:
            raise RerankerUnavailableException("cross-encoder unavailable")
        results = [
            RerankResult(doc_id=d.doc_id, score=self._scores.get(d.doc_id, 0.5)) for d in docs
        ]
        results.sort(key=lambda r: -r.score)
        return results[:top_k] if top_k is not None else results


def _orchestrator_with(
    reranker=None,
    breaker=None,
    speculative=None,
    reranker_config: RerankerConfig | None = None,
    feature_flags: FeatureFlags | None = None,
) -> SearchOrchestrator:
    """Build an orchestrator around mocks — no infrastructure required."""
    return SearchOrchestrator(
        session=AsyncMock(),
        qdrant_client=AsyncMock(),
        embedding_service=AsyncMock(),
        embedding_cache=AsyncMock(),
        config=SearchConfig(),
        reranker=reranker,
        circuit_breaker=breaker,
        speculative_reranker=speculative,
        reranker_config=reranker_config or RerankerConfig(speculative_enabled=False),
        feature_flags=feature_flags or FeatureFlags(),
    )


class TestRerankIntegration:
    """C-05: rerank applied, skipped, and degraded inside POST /search."""

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_rerank_applied_reorders_hits(self, mock_lex, mock_vec):
        """rerank=true promotes the cross-encoder's favourite to the top."""
        d1, d2 = uuid4(), uuid4()
        # RRF would put d1 first (it is in both channels); rerank puts d2 first.
        mock_lex.return_value = [_lex_hit(d1, score=1.5), _lex_hit(d2, score=1.0)]
        mock_vec.return_value = [_vec_hit(d1, score=0.95), _vec_hit(d2, score=0.5)]
        reranker = MockRerankerService({d1: 0.1, d2: 0.9})

        resp = await _orchestrator_with(reranker=reranker).search(
            _make_request(rerank=True, explain=True)
        )

        assert [h.doc_id for h in resp.hits] == [d2, d1]
        assert len(reranker.calls) == 1
        debug = resp.hits[0].debug
        assert debug["rerank_applied"] is True
        assert debug["rerank_degraded"] is False
        assert debug["rerank_score"] == 0.9
        # The pre-rerank fusion score stays visible for debugging.
        assert debug["rrf_score"] is not None

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_rerank_false_leaves_order_untouched(self, mock_lex, mock_vec):
        """MVP regression: rerank=false must not call the cross-encoder."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        reranker = MockRerankerService()

        resp = await _orchestrator_with(reranker=reranker).search(
            _make_request(rerank=False, explain=True)
        )

        assert reranker.calls == []
        debug = resp.hits[0].debug
        assert debug["rerank_applied"] is False
        assert debug["rerank_degraded"] is False
        assert debug["rerank_score"] is None

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_circuit_breaker_open_skips_rerank(self, mock_lex, mock_vec):
        """An open breaker means RerankerService is never called (C-03)."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        breaker = RerankerCircuitBreaker(CircuitBreakerConfig())
        breaker._state = "open"
        reranker = MockRerankerService()

        resp = await _orchestrator_with(reranker=reranker, breaker=breaker).search(
            _make_request(rerank=True, explain=True)
        )

        assert reranker.calls == [], "reranker must not be called while breaker is open"
        assert len(resp.hits) == 1
        debug = resp.hits[0].debug
        assert debug["rerank_degraded"] is True
        assert debug["rerank_applied"] is False
        # Per C-05 a breaker trip is not a channel failure.
        assert resp.degraded is False

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_breaker_opens_on_errors_then_degrades(self, mock_lex, mock_vec):
        """C-05: mock errors open the breaker, the next request degrades."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        breaker = RerankerCircuitBreaker(CircuitBreakerConfig())
        orchestrator = _orchestrator_with(reranker=MockRerankerService(fail=True), breaker=breaker)

        first = await orchestrator.search(_make_request(rerank=True, explain=True))
        assert breaker.is_open() is True
        assert first.hits[0].debug["rerank_applied"] is False
        assert first.degraded is False, "reranker failure must not degrade the channels"

        # A healthy reranker now, but the breaker is open → degraded.
        orchestrator._reranker = MockRerankerService()
        second = await orchestrator.search(_make_request(rerank=True, explain=True))
        assert second.hits[0].debug["rerank_degraded"] is True

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_weighted_fusion_combined_with_rerank(self, mock_lex, mock_vec):
        """fusion=weighted + rerank=true + alpha=0.7 work together."""
        d1, d2 = uuid4(), uuid4()
        mock_lex.return_value = [_lex_hit(d1, score=1.5), _lex_hit(d2, score=0.5)]
        mock_vec.return_value = [_vec_hit(d1, score=0.1), _vec_hit(d2, score=0.8)]
        reranker = MockRerankerService({d1: 0.2, d2: 0.8})

        resp = await _orchestrator_with(reranker=reranker).search(
            _make_request(fusion="weighted", fusion_alpha=0.7, rerank=True, explain=True)
        )

        assert [h.doc_id for h in resp.hits] == [d2, d1]
        debug = resp.hits[0].debug
        assert debug["fusion_strategy"] == "weighted"
        assert debug["fusion_alpha"] == 0.7
        assert debug["weighted_score"] is not None
        assert debug["rerank_applied"] is True

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_missing_reranker_degrades_instead_of_failing(self, mock_lex, mock_vec):
        """No GPU / no injected service must not produce a 500."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]

        resp = await _orchestrator_with(reranker=None).search(
            _make_request(rerank=True, explain=True)
        )

        assert len(resp.hits) == 1
        assert resp.hits[0].debug["rerank_degraded"] is True
        assert resp.degraded is False

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_global_flag_disables_rerank(self, mock_lex, mock_vec):
        """FeatureFlags.rerank_enabled=False is a process-wide kill switch."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        reranker = MockRerankerService()

        resp = await _orchestrator_with(
            reranker=reranker, feature_flags=FeatureFlags(rerank_enabled=False)
        ).search(_make_request(rerank=True, explain=True))

        assert reranker.calls == []
        assert resp.hits[0].debug["rerank_degraded"] is True

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_speculative_path_scores_every_fused_doc(self, mock_lex, mock_vec):
        """C-04 speculative covers lex top-N; the orchestrator finishes the tail."""
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        mock_lex.return_value = [_lex_hit(d1), _lex_hit(d2)]
        mock_vec.return_value = [_vec_hit(d3)]
        reranker = MockRerankerService({d1: 0.3, d2: 0.2, d3: 0.9})
        breaker = RerankerCircuitBreaker(CircuitBreakerConfig())
        cfg = RerankerConfig(speculative_enabled=True, speculative_top_n=10)
        speculative = SpeculativeReranker(reranker, breaker, cfg)

        resp = await _orchestrator_with(
            reranker=reranker, breaker=breaker, speculative=speculative, reranker_config=cfg
        ).search(_make_request(rerank=True, explain=True))

        # Every fused document ends up with a cross-encoder score.
        assert [h.doc_id for h in resp.hits] == [d3, d1, d2]
        for hit in resp.hits:
            assert hit.debug["rerank_applied"] is True
            assert hit.debug["rerank_score"] is not None

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_ten_consecutive_rerank_requests_are_stable(self, mock_lex, mock_vec):
        """C-05: 10 back-to-back rerank requests all succeed."""
        docs = {uuid4() for _ in range(5)}
        mock_lex.return_value = [_lex_hit(d, score=1.0) for d in docs]
        mock_vec.return_value = [_vec_hit(d, score=0.9) for d in docs]
        orchestrator = _orchestrator_with(
            reranker=MockRerankerService(), breaker=RerankerCircuitBreaker(CircuitBreakerConfig())
        )

        for _ in range(10):
            resp = await orchestrator.search(_make_request(rerank=True, explain=True))
            assert len(resp.hits) == 5
            assert resp.degraded is False
            assert resp.hits[0].debug["rerank_applied"] is True

    @pytest.mark.asyncio
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_rerank_does_not_resurrect_failed_channel(self, mock_lex, mock_vec):
        """A lost vector channel stays degraded even though rerank succeeded."""
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.side_effect = QdrantUnavailableError("qdrant down")
        reranker = MockRerankerService({d1: 0.7})

        resp = await _orchestrator_with(reranker=reranker).search(
            _make_request(rerank=True, explain=True)
        )

        assert resp.degraded is True, "vector channel loss is the only degraded trigger"
        assert resp.total_vector == 0
        assert resp.hits[0].debug["rerank_applied"] is True
