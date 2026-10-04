"""Integration tests for nightly eval job (C-07)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.api.schemas import SearchHit, SearchResponse
from app.config import AppConfig, EvalConfig, SearchConfig
from app.db.queries import eval as eval_queries
from app.db.session import async_session_factory
from app.eval.datasets import EvalQuery
from app.eval.nightly import EvalReport, NightlyEvalJob

pytestmark = pytest.mark.slow


class TestNightlyEvalJob:
    """Test NightlyEvalJob integration."""

    @pytest.fixture
    def mock_search_response(self):
        """Create a mock search response for testing."""
        doc1 = uuid4()
        doc2 = uuid4()
        doc3 = uuid4()
        return SearchResponse(
            hits=[
                SearchHit(
                    doc_id=doc1,
                    score=0.9,
                    title="Title 1",
                    snippet="Snippet 1",
                    attributes={},
                ),
                SearchHit(
                    doc_id=doc2,
                    score=0.8,
                    title="Title 2",
                    snippet="Snippet 2",
                    attributes={},
                ),
                SearchHit(
                    doc_id=doc3,
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
    def eval_job(self):
        """Create a test eval job instance."""
        mock_app_config = MagicMock(spec=AppConfig)
        mock_app_config.feature_flags = MagicMock()
        mock_app_config.feature_flags.vector_search_enabled = True
        mock_app_config.feature_flags.rerank_enabled = False

        # Mock the eval config
        mock_eval_config = MagicMock(spec=EvalConfig)
        mock_eval_config.block_release = True
        mock_eval_config.dataset_path = "test_path"
        mock_eval_config.recall_regression_threshold = 0.1
        mock_eval_config.ndcg_regression_threshold = 0.1
        mock_app_config.eval = mock_eval_config

        mock_search_config = MagicMock(spec=SearchConfig)
        mock_search_config.top_k = 10
        mock_search_config.timeout_ms = 1000

        job = NightlyEvalJob(
            app_config=mock_app_config,
            search_config=mock_search_config,
            session_factory=async_session_factory,
        )

        # Mock the qdrant service
        job._qdrant_service.get_async_client = MagicMock()

        # Mock the orchestrator creation
        mock_orchestrator = MagicMock()
        job._orchestrator = mock_orchestrator
        return job

    @pytest.fixture
    def eval_queries_data(self):
        """Create test evaluation queries."""
        doc1 = uuid4()
        doc2 = uuid4()
        doc3 = uuid4()
        return [
            EvalQuery(
                query_id="q001",
                query="test query 1",
                tenant_id="550e8400-e29b-41d4-a716-446655440000",
                relevant_doc_ids=[str(doc1), str(doc2)],
                language="en",
            ),
            EvalQuery(
                query_id="q002",
                query="test query 2",
                tenant_id="550e8400-e29b-41d4-a716-446655440000",
                relevant_doc_ids=[str(doc2), str(doc3)],
                language="en",
            ),
        ]

    @pytest.mark.asyncio
    async def test_run_rrf_strategy(self, eval_job, eval_queries_data, monkeypatch):
        """Test running eval with RRF strategy."""
        # Mock the dataset loading
        monkeypatch.setattr("app.eval.nightly.load_dataset", lambda _path: eval_queries_data)
    
        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()
        eval_queries.get_baseline = AsyncMock(return_value=None)
        
# Mock the orchestrator to return relevant docs for both queries
        def mock_search(request):
            if "test query 1" in request.query:
                return SearchResponse(
                    hits=[
                        SearchHit(
                            doc_id=eval_queries_data[0].relevant_doc_ids[0],
                            score=0.9,
                            title="Title 1",
                            snippet="Snippet 1",
                            attributes={},
                        ),
                        SearchHit(
                            doc_id=eval_queries_data[0].relevant_doc_ids[1],
                            score=0.8,
                            title="Title 2",
                            snippet="Snippet 2",
                            attributes={},
                        ),
                    ],
                    total_lexical=2,
                    total_vector=2,
                    latency_ms=100,
                    degraded=False,
                    partial=False,
                )
            else:  # test query 2
                return SearchResponse(
                    hits=[
                        SearchHit(
                            doc_id=eval_queries_data[1].relevant_doc_ids[0],
                            score=0.9,
                            title="Title 2",
                            snippet="Snippet 2",
                            attributes={},
                        ),
                        SearchHit(
                            doc_id=eval_queries_data[1].relevant_doc_ids[1],
                            score=0.8,
                            title="Title 3",
                            snippet="Snippet 3",
                            attributes={},
                        ),
                    ],
                    total_lexical=2,
                    total_vector=2,
                    latency_ms=100,
                    degraded=False,
                    partial=False,
                )
        
        # Mock the orchestrator creation and search method
        orchestrator_mock = AsyncMock()
        orchestrator_mock.search = AsyncMock(side_effect=mock_search)
        monkeypatch.setattr(eval_job, "_orchestrator", orchestrator_mock)
    
        # Run the evaluation
        report = await eval_job.run()
    
        # Verify results
        assert isinstance(report, EvalReport)
        assert "rrf" in report.strategies
        assert report.strategies["rrf"]["recall_at_10"] == 1.0  # Both relevant docs retrieved for both queries
        assert report.strategies["rrf"]["ndcg_at_10"] > 0
        assert report.strategies["rrf"]["mrr"] == 1.0  # q001: doc1 rank1 (1.0), q002: doc2 rank1 (1.0) → avg 1.0
    
        # Verify orchestrator was called for each query and strategy
        assert eval_job._orchestrator.search.call_count == 6  # 2 queries × 3 strategies

    @pytest.mark.asyncio
    async def test_run_weighted_strategy(self, eval_job, eval_queries_data, monkeypatch):
        """Test running eval with weighted strategy."""
        # Mock the dataset loading
        monkeypatch.setattr("app.eval.nightly.load_dataset", lambda _path: eval_queries_data)
    
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
                    doc_id=eval_queries_data[0].relevant_doc_ids[1],
                    score=0.9,
                    title="Title 2",
                    snippet="Snippet 2",
                    attributes={},
                ),
                SearchHit(
                    doc_id=eval_queries_data[0].relevant_doc_ids[0],
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
        
        # Mock the orchestrator creation and search method
        orchestrator_mock = AsyncMock()
        orchestrator_mock.search = AsyncMock(return_value=mock_response)
        monkeypatch.setattr(eval_job, "_orchestrator", orchestrator_mock)
    
        # Run the evaluation
        report = await eval_job.run()
    
        # Verify results
        assert "weighted" in report.strategies
        assert report.strategies["weighted"]["recall_at_10"] == 0.75  # q001: both retrieved (1.0), q002: doc2 only (0.5) → avg 0.75
        assert report.strategies["weighted"]["mrr"] == 1.0  # q001: doc2 rank1 (1.0), q002: doc2 rank1 (1.0) → avg 1.0

    @pytest.mark.asyncio
    async def test_regression_detection(self, eval_job, eval_queries_data, monkeypatch):
        """Test regression detection against baseline."""
        # Mock the dataset loading
        monkeypatch.setattr("app.eval.nightly.load_dataset", lambda _path: eval_queries_data)
    
        # Mock search to return irrelevant docs (causing low recall)
        irrelevant1 = uuid4()
        irrelevant2 = uuid4()
        mock_irrelevant_response = SearchResponse(
            hits=[
                SearchHit(
                    doc_id=irrelevant1,
                    score=0.9,
                    title="Irrelevant 1",
                    snippet="Irrelevant snippet 1",
                    attributes={},
                ),
                SearchHit(
                    doc_id=irrelevant2,
                    score=0.8,
                    title="Irrelevant 2",
                    snippet="Irrelevant snippet 2",
                    attributes={},
                ),
            ],
            total_lexical=2,
            total_vector=2,
            latency_ms=100,
            degraded=False,
            partial=False,
        )
        eval_job._orchestrator.search = AsyncMock(return_value=mock_irrelevant_response)
    
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
    async def test_no_regression_when_baseline_better(self, eval_job, eval_queries_data, monkeypatch):
        """Test no regression when current is better than baseline."""
        # Mock the dataset loading
        monkeypatch.setattr("app.eval.nightly.load_dataset", lambda _path: eval_queries_data)
    
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
    
        # Mock the orchestrator to return relevant docs for both queries
        def mock_search(request):
            if "test query 1" in request.query:
                return SearchResponse(
                    hits=[
                        SearchHit(
                            doc_id=eval_queries_data[0].relevant_doc_ids[0],
                            score=0.9,
                            title="Title 1",
                            snippet="Snippet 1",
                            attributes={},
                        ),
                        SearchHit(
                            doc_id=eval_queries_data[0].relevant_doc_ids[1],
                            score=0.8,
                            title="Title 2",
                            snippet="Snippet 2",
                            attributes={},
                        ),
                    ],
                    total_lexical=2,
                    total_vector=2,
                    latency_ms=100,
                    degraded=False,
                    partial=False,
                )
            else:  # test query 2
                return SearchResponse(
                    hits=[
                        SearchHit(
                            doc_id=eval_queries_data[1].relevant_doc_ids[0],
                            score=0.9,
                            title="Title 2",
                            snippet="Snippet 2",
                            attributes={},
                        ),
                        SearchHit(
                            doc_id=eval_queries_data[1].relevant_doc_ids[1],
                            score=0.8,
                            title="Title 3",
                            snippet="Snippet 3",
                            attributes={},
                        ),
                    ],
                    total_lexical=2,
                    total_vector=2,
                    latency_ms=100,
                    degraded=False,
                    partial=False,
                )
        
        # Mock the orchestrator creation and search method
        orchestrator_mock = AsyncMock()
        orchestrator_mock.search = AsyncMock(side_effect=mock_search)
        monkeypatch.setattr(eval_job, "_orchestrator", orchestrator_mock)
    
        # Run the evaluation
        report = await eval_job.run()
    
        # Should not detect regression
        assert not report.regression_detected
        assert len(report.regressions) == 0

    @pytest.mark.asyncio
    async def test_no_regression_when_no_baseline(self, eval_job, eval_queries_data, monkeypatch):
        """Test no regression when no baseline exists."""
        # Mock the dataset loading
        monkeypatch.setattr("app.eval.nightly.load_dataset", lambda _path: eval_queries_data)
    
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
    async def test_query_failure_handling(self, eval_job, eval_queries_data, monkeypatch):
        """Test handling of query evaluation failures."""
        # Mock the dataset loading
        monkeypatch.setattr("app.eval.nightly.load_dataset", lambda _path: eval_queries_data)
    
        # Mock the database operations
        mock_session = AsyncMock()
        eval_job._session_factory = MagicMock(return_value=mock_session)
        eval_queries.register_dataset = AsyncMock()
        eval_queries.save_result = AsyncMock()
        eval_queries.get_baseline = AsyncMock(return_value=None)
    
        # Make one query succeed (with both relevant docs), one fail
        mock_response_success = SearchResponse(
            hits=[
                SearchHit(
                    doc_id=eval_queries_data[0].relevant_doc_ids[0],
                    score=0.9,
                    title="Title 1",
                    snippet="Snippet 1",
                    attributes={},
                ),
                SearchHit(
                    doc_id=eval_queries_data[0].relevant_doc_ids[1],
                    score=0.8,
                    title="Title 2",
                    snippet="Snippet 2",
                    attributes={},
                ),
            ],
            total_lexical=2,
            total_vector=2,
            latency_ms=100,
            degraded=False,
            partial=False,
        )
        
        # Mock the orchestrator creation and search method with side effect
        orchestrator_mock = AsyncMock()
        orchestrator_mock.search.side_effect = [
            mock_response_success,
            Exception("Query failed"),
        ]
        monkeypatch.setattr(eval_job, "_orchestrator", orchestrator_mock)
    
        # Run the evaluation
        report = await eval_job.run()
    
        # Should still complete but with lower metrics due to failure
        assert isinstance(report, EvalReport)
        assert report.queries_processed == 2
        # One query succeeded (recall=1.0), one failed (recall=0.0) -> average=0.5
        assert report.strategies["rrf"]["recall_at_10"] == 0.5