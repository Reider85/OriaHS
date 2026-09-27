"""Unit tests for degraded mode (C-09)."""

import asyncio
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from app.api.schemas import SearchRequest, SearchFilters
from app.config import FeatureFlags
from app.search.exceptions import QdrantUnavailableError
from app.search.orchestrator import SearchOrchestrator, _ChannelOutcome
from app.search.lexical import LexicalHit
from app.search.vector import VectorHit
from app.observability import metrics


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
        self, 
        mock_lexical_hits, 
        mock_vector_hits, 
        search_request
    ):
        """Test that vector_search_enabled=False skips vector channel entirely."""
        # Setup orchestrator with vector disabled
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=AsyncMock(),
            feature_flags=FeatureFlags(vector_search_enabled=False),
        )

        # Mock lexical and vector channels
        lexical_task = asyncio.create_task(lambda: mock_lexical_hits)
        vector_task = asyncio.create_task(lambda: mock_vector_hits)
        
        # Mock the pipeline to return the hits
        with patch.object(orchestrator, '_run_pipeline') as mock_pipeline:
            mock_pipeline.return_value = _ChannelOutcome(
                lex_hits=mock_lexical_hits,
                vec_hits=[],
                lex_done=True,
                vec_done=True,
            )
            
            result = await orchestrator.search(search_request)
            
            # Verify vector channel was skipped
            assert result.total_vector == 0
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.degraded == True
            assert result.partial == False
            
            # Verify metric was incremented
            assert metrics.search_degraded_total.labels(reason="vector_disabled")._value._value > 0

    @pytest.mark.asyncio
    async def test_qdrant_health_check_fail(
        self, 
        mock_lexical_hits, 
        search_request
    ):
        """Test that Qdrant health check failure skips vector channel."""
        # Setup orchestrator with failing health check
        mock_qdrant = AsyncMock()
        mock_qdrant.get_collection_info.side_effect = Exception("Qdrant unavailable")
        
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=mock_qdrant,
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=AsyncMock(),
            feature_flags=FeatureFlags(vector_search_enabled=True),
        )

        # Mock lexical channel
        lexical_task = asyncio.create_task(lambda: mock_lexical_hits)
        
        # Mock the pipeline to return the hits
        with patch.object(orchestrator, '_run_pipeline') as mock_pipeline:
            mock_pipeline.return_value = _ChannelOutcome(
                lex_hits=mock_lexical_hits,
                vec_hits=[],
                lex_done=True,
                vec_done=True,
            )
            
            result = await orchestrator.search(search_request)
            
            # Verify vector channel was skipped due to health check
            assert result.total_vector == 0
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.degraded == True
            assert result.partial == False
            
            # Verify metric was incremented
            assert metrics.search_degraded_total.labels(reason="qdrant_unavailable")._value._value > 0

    @pytest.mark.asyncio
    async def test_deadline_exceeded_partial_results(
        self, 
        mock_lexical_hits, 
        search_request
    ):
        """Test that deadline exceeded returns partial results."""
        # Setup orchestrator with very short timeout
        search_request.timeout_ms = 10  # Very short timeout
        
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=AsyncMock(),
            feature_flags=FeatureFlags(vector_search_enabled=True),
        )

        # Mock the pipeline to simulate timeout
        with patch.object(orchestrator, '_run_pipeline') as mock_pipeline:
            mock_pipeline.return_value = _ChannelOutcome(
                lex_hits=mock_lexical_hits,
                vec_hits=[],
                lex_done=False,  # Lexical didn't finish in time
                vec_done=False,  # Vector didn't finish in time
                partial=True,    # Deadline exceeded
            )
            
            result = await orchestrator.search(search_request)
            
            # Verify partial results
            assert result.partial == True
            assert result.degraded == True  # Both channels failed
            
            # Verify metric was incremented
            assert metrics.search_partial_total._value._value > 0

    @pytest.mark.asyncio
    async def test_both_channels_work_normally(
        self, 
        mock_lexical_hits, 
        mock_vector_hits, 
        search_request
    ):
        """Test that both channels work normally when enabled and healthy."""
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=AsyncMock(),
            feature_flags=FeatureFlags(vector_search_enabled=True),
        )

        # Mock the pipeline to return both channels
        with patch.object(orchestrator, '_run_pipeline') as mock_pipeline:
            mock_pipeline.return_value = _ChannelOutcome(
                lex_hits=mock_lexical_hits,
                vec_hits=mock_vector_hits,
                lex_done=True,
                vec_done=True,
            )
            
            result = await orchestrator.search(search_request)
            
            # Verify both channels worked
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.total_vector == len(mock_vector_hits)
            assert result.degraded == False
            assert result.partial == False
            
            # Verify metrics were not incremented
            assert metrics.search_degraded_total._value._value == 0
            assert metrics.search_partial_total._value._value == 0

    @pytest.mark.asyncio
    async def test_qdrant_exception_during_search(
        self, 
        mock_lexical_hits, 
        search_request
    ):
        """Test that Qdrant exception during search triggers degraded mode."""
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=AsyncMock(),
            feature_flags=FeatureFlags(vector_search_enabled=True),
        )

        # Mock the pipeline to simulate Qdrant exception
        with patch.object(orchestrator, '_run_pipeline') as mock_pipeline:
            mock_pipeline.return_value = _ChannelOutcome(
                lex_hits=mock_lexical_hits,
                vec_hits=[],
                lex_done=True,
                vec_done=True,
                vec_error=QdrantUnavailableError("Qdrant failed"),
            )
            
            result = await orchestrator.search(search_request)
            
            # Verify degraded mode was triggered
            assert result.total_vector == 0
            assert result.total_lexical == len(mock_lexical_hits)
            assert result.degraded == True
            assert result.partial == False