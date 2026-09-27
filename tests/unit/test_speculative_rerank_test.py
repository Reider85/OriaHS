"""C-04: Speculative rerank unit tests (ARCHITECT §6.5).

Tests speculative reranking: lex-first normal path, vec-first fallback, circuit
breaker open, empty inputs, error handling, latency advantage, concurrency.
"""

import asyncio
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.config import RerankerConfig
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.exceptions import RerankerTimeoutException, RerankerUnavailableException
from app.reranker.schemas import RerankCandidate, RerankResult
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.lexical import LexicalHit
from app.search.speculative import SpeculativeReranker
from app.search.vector import VectorHit


class MockRerankerService:
    """Mock reranker service for testing."""
    
    def __init__(self, delay_ms: float = 0):
        self.delay_ms = delay_ms
        self.rerank_calls = []
    
    async def rerank(self, query: str, docs: list[RerankCandidate], top_k: int | None = None) -> list[RerankResult]:
        """Mock rerank with optional delay."""
        self.rerank_calls.append((query, docs, top_k))
        
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000.0)
        
        results = []
        for i, doc in enumerate(docs):
            score = 1.0 - (i * 0.1)  # Mock scores: 1.0, 0.9, 0.8, ...
            results.append(RerankResult(doc_id=doc.doc_id, score=score))
        
        return results


class TestSpeculativeReranker:
    """Test SpeculativeReranker core functionality."""

    def test_init(self) -> None:
        """Test SpeculativeReranker initialization."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        assert speculative_reranker._reranker is reranker_service
        assert speculative_reranker._circuit_breaker is circuit_breaker
        assert speculative_reranker._config is config

    async def test_speculative_rerank_lex_first_normal_path(self) -> None:
        """Test normal path: lexical finishes first, speculative rerank runs."""
        reranker_service = MockRerankerService(delay_ms=50)
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig(speculative_top_n=5)
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        lex_hits = [
            LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1"),
            LexicalHit(doc_id=d2, score=0.6, title="doc2", content_snippet="content2"),
            LexicalHit(doc_id=d3, score=0.4, title="doc3", content_snippet="content3"),
        ]
        vec_hits = [
            VectorHit(doc_id=d1, score=0.7, title="doc1", content_snippet="content1"),
            VectorHit(doc_id=d2, score=0.5, title="doc2", content_snippet="content2"),
        ]
        
        async def mock_lex_task():
            await asyncio.sleep(0.01)  # Lexical finishes first (10ms)
            return lex_hits
        
        async def mock_vec_task():
            await asyncio.sleep(0.05)  # Vector finishes later (50ms)
            return vec_hits
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        
        # Verify results
        assert len(rerank_results) == 3  # rerank top-5, but only 3 lex hits
        assert rerank_results[0].doc_id == d1  # highest score first
        assert rerank_results[0].score == 1.0
        assert rerank_results[1].doc_id == d2
        assert rerank_results[1].score == 0.9
        assert rerank_results[2].doc_id == d3
        assert rerank_results[2].score == 0.8
        
        # Verify rerank candidates were built correctly
        assert len(reranker_service.rerank_calls) == 1
        rerank_call = reranker_service.rerank_calls[0]
        assert rerank_call[0] == "test query"
        assert len(rerank_call[1]) == 3  # 3 lex hits
        assert rerank_call[1][0].doc_id == d1
        assert "doc1\ncontent1" in rerank_call[1][0].text

    async def test_speculative_rerank_vec_first_fallback(self) -> None:
        """Test fallback path: vector finishes first, rerank full fusion results."""
        reranker_service = MockRerankerService(delay_ms=50)
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig(speculative_top_n=5)
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks
        d1, d2, d3, d4 = uuid4(), uuid4(), uuid4(), uuid4()
        lex_hits = [
            LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1"),
            LexicalHit(doc_id=d2, score=0.6, title="doc2", content_snippet="content2"),
        ]
        vec_hits = [
            VectorHit(doc_id=d1, score=0.7, title="doc1", content_snippet="content1"),
            VectorHit(doc_id=d2, score=0.5, title="doc2", content_snippet="content2"),
            VectorHit(doc_id=d3, score=0.4, title="doc3", content_snippet="content3"),
            VectorHit(doc_id=d4, score=0.3, title="doc4", content_snippet="content4"),
        ]
        
        async def mock_lex_task():
            await asyncio.sleep(0.05)  # Lexical finishes later (50ms)
            return lex_hits
        
        async def mock_vec_task():
            await asyncio.sleep(0.01)  # Vector finishes first (10ms)
            return vec_hits
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        
        # Verify fallback behavior: rerank full fusion results
        assert len(rerank_results) == 4  # rerank full fusion top-50, but only 4 unique docs
        assert rerank_results[0].doc_id == d1  # highest score first
        assert rerank_results[0].score == 1.0
        assert rerank_results[1].doc_id == d2
        assert rerank_results[1].score == 0.9
        assert rerank_results[2].doc_id == d3
        assert rerank_results[2].score == 0.8
        assert rerank_results[3].doc_id == d4
        assert rerank_results[3].score == 0.7
        
        # Verify rerank candidates were built from fusion results
        assert len(reranker_service.rerank_calls) == 1
        rerank_call = reranker_service.rerank_calls[0]
        assert len(rerank_call[1]) == 4  # 4 unique docs from fusion

    async def test_speculative_rerank_circuit_breaker_open(self) -> None:
        """Test circuit breaker open: skip rerank entirely."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        # Force circuit breaker open
        circuit_breaker._state = "open"
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks
        d1 = uuid4()
        lex_hits = [LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1")]
        vec_hits = [VectorHit(doc_id=d1, score=0.7, title="doc1", content_snippet="content1")]
        
        async def mock_lex_task():
            await asyncio.sleep(0.01)
            return lex_hits
        
        async def mock_vec_task():
            await asyncio.sleep(0.02)
            return vec_hits
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        
        # Verify rerank is skipped when circuit breaker is open
        assert len(rerank_results) == 0
        assert len(reranker_service.rerank_calls) == 0  # No rerank calls made

    async def test_speculative_rerank_empty_lexical(self) -> None:
        """Test empty lexical results: no speculative rerank."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks with empty lexical results
        d1 = uuid4()
        lex_hits = []
        vec_hits = [VectorHit(doc_id=d1, score=0.7, title="doc1", content_snippet="content1")]
        
        async def mock_lex_task():
            await asyncio.sleep(0.01)
            return lex_hits
        
        async def mock_vec_task():
            await asyncio.sleep(0.02)
            return vec_hits
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        
        # Verify no rerank when no lexical results
        assert len(rerank_results) == 0
        assert len(reranker_service.rerank_calls) == 0

    async def test_speculative_rerank_vector_failure(self) -> None:
        """Test vector search failure: return empty rerank results."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks with vector failure
        d1 = uuid4()
        lex_hits = [LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1")]
        
        async def mock_lex_task():
            await asyncio.sleep(0.01)
            return lex_hits
        
        async def mock_vec_task():
            await asyncio.sleep(0.01)
            raise QdrantUnavailableError("Vector search failed")
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        
        # Verify empty rerank results when vector fails
        assert len(rerank_results) == 0
        assert len(reranker_service.rerank_calls) == 0
        assert len(vec_results) == 0  # Vector task failed, so empty results

    async def test_speculative_rerank_lexical_failure(self) -> None:
        """Test lexical search failure: fallback to full rerank on vector results."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks with lexical failure
        d1, d2 = uuid4(), uuid4()
        lex_hits = []
        vec_hits = [
            VectorHit(doc_id=d1, score=0.7, title="doc1", content_snippet="content1"),
            VectorHit(doc_id=d2, score=0.5, title="doc2", content_snippet="content2"),
        ]
        
        async def mock_lex_task():
            await asyncio.sleep(0.01)
            raise Exception("Lexical search failed")
        
        async def mock_vec_task():
            await asyncio.sleep(0.02)
            return vec_hits
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        
        # Verify fallback to full rerank on vector results
        assert len(rerank_results) == 2
        assert rerank_results[0].doc_id == d1
        assert rerank_results[0].score == 1.0
        assert rerank_results[1].doc_id == d2
        assert rerank_results[1].score == 0.9

    async def test_speculative_rerank_latency_advantage(self) -> None:
        """Test latency advantage of speculative reranking."""
        reranker_service = MockRerankerService(delay_ms=100)  # 100ms rerank delay
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create mock tasks with realistic timing
        d1, d2 = uuid4(), uuid4()
        lex_hits = [LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1")]
        vec_hits = [VectorHit(doc_id=d2, score=0.7, title="doc2", content_snippet="content2")]
        
        async def mock_lex_task():
            await asyncio.sleep(0.01)  # Lexical finishes quickly (10ms)
            return lex_hits
        
        async def mock_vec_task():
            await asyncio.sleep(0.05)  # Vector takes longer (50ms)
            return vec_hits
        
        lex_task = asyncio.create_task(mock_lex_task())
        vec_task = asyncio.create_task(mock_vec_task())
        
        start_time = time.time()
        vec_results, rerank_results = await speculative_reranker.run("test query", lex_task, vec_task)
        end_time = time.time()
        
        total_time_ms = (end_time - start_time) * 1000
        
        # Verify speculative reranking saves time
        # Should be ~60ms (10ms lex + 50ms vec) vs ~150ms (sequential)
        assert total_time_ms < 120  # Should be significantly less than sequential
        assert len(rerank_results) == 1
        assert rerank_results[0].doc_id == d1

    async def test_speculative_rerank_concurrent_calls_no_race_conditions(self) -> None:
        """Test concurrent calls to speculative reranker - no race conditions."""
        reranker_service = MockRerankerService(delay_ms=10)
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Create multiple concurrent tasks
        tasks = []
        for i in range(10):
            d = uuid4()
            lex_hits = [LexicalHit(doc_id=d, score=0.8, title=f"doc{i}", content_snippet=f"content{i}")]
            vec_hits = [VectorHit(doc_id=d, score=0.7, title=f"doc{i}", content_snippet=f"content{i}")]
            
            async def mock_lex_task(hits=lex_hits):
                await asyncio.sleep(0.01)
                return hits
            
            async def mock_vec_task(hits=vec_hits):
                await asyncio.sleep(0.02)
                return hits
            
            lex_task = asyncio.create_task(mock_lex_task())
            vec_task = asyncio.create_task(mock_vec_task())
            
            task = asyncio.create_task(speculative_reranker.run(f"query{i}", lex_task, vec_task))
            tasks.append(task)
        
        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify all tasks completed successfully
        for result in results:
            assert not isinstance(result, Exception)
            vec_hits, rerank_results = result
            assert len(rerank_results) == 1
            assert rerank_results[0].score == 1.0

    def test_build_candidates_from_lexical(self) -> None:
        """Test _build_candidates_from_lexical helper method."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Test with empty list
        candidates = speculative_reranker._build_candidates_from_lexical([])
        assert candidates == []
        
        # Test with real hits
        d1, d2 = uuid4(), uuid4()
        lex_hits = [
            LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1"),
            LexicalHit(doc_id=d2, score=0.6, title="doc2", content_snippet="content2"),
        ]
        
        candidates = speculative_reranker._build_candidates_from_lexical(lex_hits)
        
        assert len(candidates) == 2
        assert candidates[0].doc_id == d1
        assert candidates[0].score == 0.8
        assert "doc1\ncontent1" in candidates[0].text
        assert candidates[1].doc_id == d2
        assert candidates[1].score == 0.6
        assert "doc2\ncontent2" in candidates[1].text

    def test_build_candidates_from_fusion(self) -> None:
        """Test _build_candidates_from_fusion helper method."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        config = RerankerConfig()
        
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        
        # Test with empty lists
        candidates = speculative_reranker._build_candidates_from_fusion([], [])
        assert candidates == []
        
        # Test with deduplication
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        lex_hits = [
            LexicalHit(doc_id=d1, score=0.8, title="doc1", content_snippet="content1"),
            LexicalHit(doc_id=d2, score=0.6, title="doc2", content_snippet="content2"),
        ]
        vec_hits = [
            VectorHit(doc_id=d1, score=0.7, title="doc1", content_snippet="content1"),  # duplicate
            VectorHit(doc_id=d3, score=0.5, title="doc3", content_snippet="content3"),  # new
        ]
        
        candidates = speculative_reranker._build_candidates_from_fusion(lex_hits, vec_hits)
        
        assert len(candidates) == 3  # d1, d2, d3 (d1 deduplicated)
        assert candidates[0].doc_id == d1
        assert candidates[1].doc_id == d2
        assert candidates[2].doc_id == d3
        
        # Test with more than 50 candidates (should be truncated)
        many_lex = []
        many_vec = []
        for i in range(60):
            d = uuid4()
            many_lex.append(LexicalHit(doc_id=d, score=0.8, title=f"doc{i}", content_snippet=f"content{i}"))
            many_vec.append(VectorHit(doc_id=d, score=0.7, title=f"doc{i}", content_snippet=f"content{i}"))
        
        candidates = speculative_reranker._build_candidates_from_fusion(many_lex, many_vec)
        assert len(candidates) == 50  # truncated to top-50


class TestSpeculativeRerankerConfig:
    """Test SpeculativeReranker configuration."""
    
    def test_speculative_top_n_config(self) -> None:
        """Test speculative_top_n configuration."""
        reranker_service = MockRerankerService()
        circuit_breaker = RerankerCircuitBreaker(RerankerConfig())
        
        # Test default
        config = RerankerConfig()
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        assert speculative_reranker._config.speculative_top_n == 10
        
        # Test custom
        config = RerankerConfig(speculative_top_n=5)
        speculative_reranker = SpeculativeReranker(reranker_service, circuit_breaker, config)
        assert speculative_reranker._config.speculative_top_n == 5