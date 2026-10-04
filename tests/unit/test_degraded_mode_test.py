"""Unit tests for degraded mode (C-09)."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.schemas import SearchRequest
from app.config import FeatureFlags, SearchConfig
from app.observability import metrics
from app.search.exceptions import QdrantUnavailableError
from app.search.lexical import LexicalHit
from app.search.orchestrator import SearchOrchestrator, _ChannelOutcome
from app.search.vector import VectorHit


def _build_orchestrator(*, vector_enabled=True, qdrant=None, flags=None):
    """Orchestrator with every collaborator mocked.

    ``_run_pipeline`` is patched by the caller with an **AsyncMock** (it is a
    coroutine function), and the two channel coroutines are mocked too:
    ``search()`` schedules them with ``asyncio.ensure_future`` *before* calling
    ``_run_pipeline``, so leaving them real spawns tasks that blow up on the
    mock session and are never retrieved.

    ``config`` must be a real ``SearchConfig`` and not a mock: fusion reads
    ``rrf_k`` from it, and a MagicMock turns every RRF score into a MagicMock
    that cannot be sorted.
    """
    orchestrator = SearchOrchestrator(
        session=AsyncMock(),
        qdrant_client=qdrant or AsyncMock(),
        embedding_service=AsyncMock(),
        embedding_cache=AsyncMock(),
        config=SearchConfig(),
        feature_flags=flags or FeatureFlags(vector_search_enabled=vector_enabled),
    )
    orchestrator._lexical_channel = AsyncMock(return_value=[])
    orchestrator._vector_channel = AsyncMock(return_value=[])
    return orchestrator


class TestDegradedMode:
    """Test degraded mode behavior: lex-only fallback, partial results, feature flags."""

    @pytest.fixture
    def mock_lexical_hits(self):
        return [
            LexicalHit(
                doc_id=uuid4(),
                score=0.8,
                title="Test document 1",
                content_snippet="Content 1",
            ),
            LexicalHit(
                doc_id=uuid4(),
                score=0.6,
                title="Test document 2",
                content_snippet="Content 2",
            ),
        ]

    @pytest.fixture
    def mock_vector_hits(self):
        return [
            VectorHit(
                doc_id=uuid4(),
                score=0.9,
                title="Vector document 1",
                content_snippet="Vector content 1",
            ),
            VectorHit(
                doc_id=uuid4(),
                score=0.7,
                title="Vector document 2",
                content_snippet="Vector content 2",
            ),
        ]

    @pytest.fixture
    def search_request(self):
        return SearchRequest(
            query="test query",
            tenant_id=uuid4(),
            top_k=10,
            timeout_ms=200,
        )

    @pytest.mark.asyncio
    async def test_vector_search_disabled_flag(
        self, mock_lexical_hits, mock_vector_hits, search_request
    ):
        """Test that vector_search_enabled=False skips vector channel entirely."""
        orchestrator = _build_orchestrator(
            vector_enabled=False,
            flags=FeatureFlags(vector_search_enabled=False),
        )

        with patch.object(
            orchestrator,
            "_run_pipeline",
            new=AsyncMock(
                return_value=_ChannelOutcome(
                    lex_hits=mock_lexical_hits,
                    vec_hits=[],
                    lex_done=True,
                    vec_done=True,
                )
            ),
        ):
            result = await orchestrator.search(search_request)

            # Verify vector channel was skipped
            assert result.total_vector == 0
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.degraded
            assert not result.partial

            # Verify metric was incremented
            assert metrics.search_degraded_total.labels(reason="vector_disabled")._value.get() > 0

    @pytest.mark.asyncio
    async def test_qdrant_health_check_fail(self, mock_lexical_hits, search_request):
        """Test that Qdrant health check failure skips vector channel."""
        mock_qdrant = AsyncMock()
        mock_qdrant.get_collection_info.side_effect = Exception("Qdrant unavailable")

        orchestrator = _build_orchestrator(qdrant=mock_qdrant)

        with patch.object(
            orchestrator,
            "_run_pipeline",
            new=AsyncMock(
                return_value=_ChannelOutcome(
                    lex_hits=mock_lexical_hits,
                    vec_hits=[],
                    lex_done=True,
                    vec_done=True,
                )
            ),
        ):
            result = await orchestrator.search(search_request)

            # Verify vector channel was skipped due to health check
            assert result.total_vector == 0
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.degraded
            assert not result.partial

            # Verify metric was incremented
            assert (
                metrics.search_degraded_total.labels(reason="qdrant_unavailable")._value.get() > 0
            )

    @pytest.mark.asyncio
    async def test_deadline_exceeded_partial_results(self, mock_lexical_hits, search_request):
        """Test that deadline exceeded returns partial results (lex done, vec pending)."""
        search_request.timeout_ms = 10  # Very short timeout

        orchestrator = _build_orchestrator()

        with patch.object(
            orchestrator,
            "_run_pipeline",
            new=AsyncMock(
                return_value=_ChannelOutcome(
                    lex_hits=mock_lexical_hits,
                    vec_hits=[],
                    lex_done=True,
                    vec_done=False,  # Vector didn't finish in time
                    partial=True,  # Deadline exceeded
                )
            ),
        ):
            result = await orchestrator.search(search_request)

            # Verify partial results
            assert result.partial
            assert result.degraded
            assert result.total_lexical == len(mock_lexical_hits)

            # search_degraded_total инкрементится в _resolve_degraded (не
            # замокан), а search_partial_total - внутри _collect_channels,
            # который здесь замокан целиком, поэтому его не проверяем.
            assert metrics.search_degraded_total.labels(reason="deadline_exceeded")._value.get() > 0

    @pytest.mark.asyncio
    async def test_both_channels_timed_out_raises(self, search_request):
        """Test that a fully timed-out search (no channel finished) raises TimeoutError."""
        orchestrator = _build_orchestrator()

        with patch.object(
            orchestrator,
            "_run_pipeline",
            new=AsyncMock(
                return_value=_ChannelOutcome(
                    lex_hits=[],
                    vec_hits=[],
                    lex_done=False,
                    vec_done=False,
                    partial=True,
                )
            ),
        ):
            with pytest.raises(TimeoutError):
                await orchestrator.search(search_request)

    @pytest.mark.asyncio
    async def test_both_channels_work_normally(
        self, mock_lexical_hits, mock_vector_hits, search_request
    ):
        """Test that both channels work normally when enabled and healthy."""
        orchestrator = _build_orchestrator()

        # Счётчики глобальные для процесса, поэтому сравниваем дельту, а не 0.
        reasons = ("vector_disabled", "qdrant_unavailable", "deadline_exceeded")
        before = {
            reason: metrics.search_degraded_total.labels(reason=reason)._value.get()
            for reason in reasons
        }
        partial_before = metrics.search_partial_total._value.get()

        with patch.object(
            orchestrator,
            "_run_pipeline",
            new=AsyncMock(
                return_value=_ChannelOutcome(
                    lex_hits=mock_lexical_hits,
                    vec_hits=mock_vector_hits,
                    lex_done=True,
                    vec_done=True,
                )
            ),
        ):
            result = await orchestrator.search(search_request)

            # Verify both channels worked
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.total_vector == len(mock_vector_hits)
            assert not result.degraded
            assert not result.partial

            # Verify degraded/partial metrics were not touched by this request
            for reason in reasons:
                assert (
                    metrics.search_degraded_total.labels(reason=reason)._value.get()
                    == before[reason]
                )
            assert metrics.search_partial_total._value.get() == partial_before

    @pytest.mark.asyncio
    async def test_qdrant_exception_during_search(self, mock_lexical_hits, search_request):
        """Test that Qdrant exception during search triggers degraded mode."""
        orchestrator = _build_orchestrator()

        with patch.object(
            orchestrator,
            "_run_pipeline",
            new=AsyncMock(
                return_value=_ChannelOutcome(
                    lex_hits=mock_lexical_hits,
                    vec_hits=[],
                    lex_done=True,
                    vec_done=True,
                    vec_error=QdrantUnavailableError("Qdrant failed"),
                )
            ),
        ):
            result = await orchestrator.search(search_request)

            # Verify degraded mode was triggered
            assert result.total_vector == 0
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.degraded
            assert not result.partial
