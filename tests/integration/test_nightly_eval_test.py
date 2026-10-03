"""Integration tests for nightly eval job (C-07)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.schemas import SearchHit, SearchResponse
from app.config import AppConfig, EvalConfig, SearchConfig
from app.db.queries import eval as eval_queries
from app.db.session import async_session_factory
from app.eval.datasets import EvalQuery
from app.eval.nightly import EvalReport, NightlyEvalJob


class TestNightlyEvalJob:
    """Test NightlyEvalJob integration."""

    @pytest.fixture
    def mock_search_response(self):
        """Create a mock search response for testing."""
        return SearchResponse(
            hits=[
                SearchHit(
                    doc_id="doc1",
                    score=0.9,
                    title="Title 1",
                    snippet="Snippet 1",
                    attributes={},
                ),
                SearchHit(
                    doc_id="doc2",
                    score=0.8,
                    title="Title 2",
                    snippet="Snippet 2",
                    attributes={},
                ),
                SearchHit(
                    doc_id="doc3",
                    score=0.7,
                    title="Title 3",
                    snippet="Snippet 3",
                    attributes={},
                ),
            ],
            total_lexical=3,
            total_vector=3,
            latency_ms=100,
            degraded=False,
            partial=False,
        )

    @pytest.fixture
    def eval_queries_data(self):
        """Create test evaluation queries."""
        return [
            EvalQuery(
                query_id="q001",
                query="test query 1",
                tenant_id="550e8400-e29b-41d4-a716-446655440000",
                relevant_doc_ids=["doc1", "doc2"],
                language="en",
            ),
            EvalQuery(
                query_id="q002",
                query="test query 2",
                tenant_id="550e8400-e29b-41d4-a716-446655440000",
                relevant_doc_ids=["doc2", "doc3"],
                language="en",
            ),
        ]

    @pytest.fixture
    def mock_orchestrator(self, mock_search_response):
        """Create a mock orchestrator that returns predictable results."""
        orchestrator = MagicMock()
        orchestrator.search = AsyncMock(return_value=mock_search_response)
        return orchestrator

    @pytest.fixture
    def eval_job(self, mock_orchestrator):
        """Create a NightlyEvalJob with mocked dependencies."""
        job = NightlyEvalJob(
            session_factory=async_session_factory,
            search_config=SearchConfig(),
            eval_config=EvalConfig(dataset_path="test.jsonl"),
            app_config=AppConfig(),
        )
        # Mock the orchestrator creation
        job._orchestrator = mock_orchestrator
        return job

    @pytest.mark.asyncio
    async def test_run_rrf_strategy(self, eval_job, eval_queries_data):
        """Test running eval with RRF strategy."""
        # Mock the dataset loading
        eval_job._load_dataset = MagicMock(return_value=eval_queries_data)

        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()
        eval_queries.get_baseline = AsyncMock(return_value=None)

        # Run the evaluation
        report = await eval_job.run()

        # Verify results
        assert isinstance(report, EvalReport)
        assert "rrf" in report.strategies
        assert report.strategies["rrf"]["recall_at_10"] == 0.5  # 1 relevant out of 2 in top-10
        assert report.strategies["rrf"]["ndcg_at_10"] > 0
        assert report.strategies["rrf"]["mrr"] == 0.5  # First relevant at rank 1

        # Verify orchestrator was called for each query
        assert eval_job._orchestrator.search.call_count == 2

    @pytest.mark.asyncio
    async def test_run_weighted_strategy(self, eval_job, eval_queries_data):
        """Test running eval with weighted strategy."""
        # Mock the dataset loading
        eval_job._load_dataset = MagicMock(return_value=eval_queries_data)

        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()
        eval_queries.get_baseline = AsyncMock(return_value=None)

        # Mock the orchestrator to return a different response for weighted
        mock_response = SearchResponse(
            hits=[
                SearchHit(
                    doc_id="doc2",
                    score=0.9,
                    title="Title 2",
                    snippet="Snippet 2",
                    attributes={},
                ),
                SearchHit(
                    doc_id="doc1",
                    score=0.8,
                    title="Title 1",
                    snippet="Snippet 1",
                    attributes={},
                ),
            ],
            total_lexical=2,
            total_vector=2,
            latency_ms=100,
            degraded=False,
            partial=False,
        )
        eval_job._orchestrator.search = AsyncMock(return_value=mock_response)

        # Run the evaluation
        report = await eval_job.run()

        # Verify results
        assert "weighted" in report.strategies
        assert report.strategies["weighted"]["recall_at_10"] == 1.0  # Both relevant docs retrieved
        assert report.strategies["weighted"]["mrr"] == 1.0  # First relevant at rank 1

    @pytest.mark.asyncio
    async def test_regression_detection(self, eval_job, eval_queries_data):
        """Test regression detection against baseline."""
        # Mock the dataset loading
        eval_job._load_dataset = MagicMock(return_value=eval_queries_data)

        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()

        # Mock baseline with better recall
        baseline_result = MagicMock()
        baseline_result.recall_at_10 = 0.8
        baseline_result.ndcg_at_10 = 0.7
        baseline_result.mrr = 0.6
        eval_queries.get_baseline = AsyncMock(return_value=baseline_result)

        # Run the evaluation
        report = await eval_job.run()

        # Should detect regression (current recall=0.5 < baseline=0.8)
        assert report.regression_detected
        assert "rrf" in report.regressions
        assert "recall_at_10" in report.regressions["rrf"]

    @pytest.mark.asyncio
    async def test_no_regression_when_baseline_better(self, eval_job, eval_queries_data):
        """Test no regression when current is better than baseline."""
        # Mock the dataset loading
        eval_job._load_dataset = MagicMock(return_value=eval_queries_data)

        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()

        # Mock baseline with worse recall
        baseline_result = MagicMock()
        baseline_result.recall_at_10 = 0.3
        baseline_result.ndcg_at_10 = 0.2
        baseline_result.mrr = 0.1
        eval_queries.get_baseline = AsyncMock(return_value=baseline_result)

        # Run the evaluation
        report = await eval_job.run()

        # Should not detect regression
        assert not report.regression_detected
        assert len(report.regressions) == 0

    @pytest.mark.asyncio
    async def test_no_regression_when_no_baseline(self, eval_job, eval_queries_data):
        """Test no regression when no baseline exists."""
        # Mock the dataset loading
        eval_job._load_dataset = MagicMock(return_value=eval_queries_data)

        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()
        eval_queries.get_baseline = AsyncMock(return_value=None)

        # Run the evaluation
        report = await eval_job.run()

        # Should not detect regression (no baseline to compare against)
        assert not report.regression_detected
        assert len(report.regressions) == 0

    @pytest.mark.asyncio
    async def test_query_failure_handling(self, eval_job, eval_queries_data):
        """Test handling of query evaluation failures."""
        # Mock the dataset loading
        eval_job._load_dataset = MagicMock(return_value=eval_queries_data)

        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()
        eval_queries.get_baseline = AsyncMock(return_value=None)

        # Make one query fail
        eval_job._orchestrator.search.side_effect = [
            SearchResponse(
                hits=[
                    SearchHit(
                        doc_id="doc1",
                        score=0.9,
                        title="Title 1",
                        snippet="Snippet 1",
                        attributes={},
                    ),
                ],
                total_lexical=1,
                total_vector=1,
                latency_ms=100,
                degraded=False,
                partial=False,
            ),
            Exception("Query failed"),
        ]

        # Run the evaluation
        report = await eval_job.run()

        # Should still complete but with lower metrics due to failure
        assert isinstance(report, EvalReport)
        assert report.queries_processed == 2
        # One query succeeded (recall=1.0), one failed (recall=0.0) -> average=0.5
        assert report.strategies["rrf"]["recall_at_10"] == 0.5
