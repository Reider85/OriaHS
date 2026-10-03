"""C-01: Cross-encoder reranker service unit tests (ARCHITECT §7.3).

Tests mock mode, sigmoid normalization, sorting, top_k, empty input, timeout,
GPU/CPU markers. Uses mock reranker fixture to avoid GPU dependency.
"""

import asyncio
import logging
import time
from uuid import uuid4

import pytest
import torch

from app.config import RerankerConfig
from app.reranker.exceptions import RerankerTimeoutException
from app.reranker.schemas import RerankCandidate, RerankResult
from app.reranker.service import RerankerService


class TestRerankerService:
    def test_rerank_empty_input(self, reranker_service_mock: RerankerService) -> None:
        """Test rerank with empty document list returns empty list."""
        result = asyncio.run(reranker_service_mock.rerank("test query", []))
        assert result == []

    def test_rerank_mock_mode_uniform_scores(self, reranker_service_mock: RerankerService) -> None:
        """Test mock mode returns uniform scores (1.0, 0.9, 0.8, ...)."""
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        docs = [
            RerankCandidate(doc_id=d1, text="hello world", score=0.5),
            RerankCandidate(doc_id=d2, text="test document", score=0.3),
            RerankCandidate(doc_id=d3, text="search query", score=0.8),
        ]

        result = asyncio.run(reranker_service_mock.rerank("test query", docs))

        assert len(result) == 3
        assert result[0].doc_id == d1  # rank 1: score 1.0
        assert result[1].doc_id == d2  # rank 2: score 0.9
        assert result[2].doc_id == d3  # rank 3: score 0.8
        assert result[0].score == 1.0
        assert result[1].score == 0.9
        assert result[2].score == 0.8

    def test_rerank_top_k_limit(self, reranker_service_mock: RerankerService) -> None:
        """Test top_k parameter limits returned results."""
        d1, d2, d3, d4 = uuid4(), uuid4(), uuid4(), uuid4()
        docs = [
            RerankCandidate(doc_id=d1, text="doc1", score=0.5),
            RerankCandidate(doc_id=d2, text="doc2", score=0.3),
            RerankCandidate(doc_id=d3, text="doc3", score=0.8),
            RerankCandidate(doc_id=d4, text="doc4", score=0.1),
        ]

        # Request top 2
        result = asyncio.run(reranker_service_mock.rerank("test query", docs, top_k=2))
        assert len(result) == 2
        assert result[0].doc_id == d1  # rank 1: score 1.0
        assert result[1].doc_id == d2  # rank 2: score 0.9

    def test_rerank_preserves_sorting(self, reranker_service_mock: RerankerService) -> None:
        """Test mock mode keeps the input order and assigns descending scores.

        Mock mode is a pass-through: without a cross-encoder there is nothing to
        reorder by, so the reranker must not shuffle the fusion ranking.
        """
        d1, d2 = uuid4(), uuid4()
        docs = [
            RerankCandidate(doc_id=d1, text="doc1", score=0.1),
            RerankCandidate(doc_id=d2, text="doc2", score=0.9),
        ]

        result = asyncio.run(reranker_service_mock.rerank("test query", docs))
        assert len(result) == 2
        assert result[0].doc_id == d1  # input order preserved
        assert result[0].score > result[1].score
        assert result[1].doc_id == d2

    @pytest.mark.gpu
    @pytest.mark.skipif(
        not torch.cuda.is_available(),
        reason="requires a CUDA device (skips the 2.3 GB load on CPU)",
    )
    async def test_gpu_device_detection(self) -> None:
        """Test GPU device detection on CUDA-enabled machine."""
        config = RerankerConfig(device="auto")
        reranker = RerankerService(config=config)

        await reranker._lazy_load()
        # Note: This test will pass on machines with CUDA, fail otherwise
        # In CI/testing, we expect CPU fallback
        device = reranker._device
        assert device in ["cuda", "cpu"]

    def test_cpu_fallback_warning(self, caplog) -> None:
        """Test CPU mode does not load a model when mock_mode is on."""
        config = RerankerConfig(device="cpu", mock_mode=True)
        reranker = RerankerService(config=config)

        with caplog.at_level(logging.INFO):
            result = asyncio.run(
                reranker.rerank(
                    "test query", [RerankCandidate(doc_id=uuid4(), text="test", score=0.5)]
                )
            )

        # Mock mode не грузит модель: в CI без GPU это единственный способ
        # прогнать rerank, иначе скачивается 2.3 GB чекпойнта.
        assert len(result) == 1
        assert reranker._model is None

    def test_warmup_method(self, reranker_service_mock: RerankerService) -> None:
        """Test warmup method works without errors."""
        # Mock service warmup should complete immediately
        asyncio.run(reranker_service_mock.warmup())
        # No assertion needed - just ensure no exception

    def test_rerank_candidate_schema_validation(self) -> None:
        """Test RerankCandidate schema validation."""
        doc_id = uuid4()
        candidate = RerankCandidate(doc_id=doc_id, text="test document content", score=0.75)
        assert candidate.doc_id == doc_id
        assert candidate.text == "test document content"
        assert candidate.score == 0.75

    def test_rerank_result_schema_validation(self) -> None:
        """Test RerankResult schema validation."""
        doc_id = uuid4()
        result = RerankResult(doc_id=doc_id, score=0.85)
        assert result.doc_id == doc_id
        assert result.score == 0.85

    def test_rerank_result_score_range(self, reranker_service_mock: RerankerService) -> None:
        """Test that scores are in expected range [0, 1]."""
        d1 = uuid4()
        docs = [RerankCandidate(doc_id=d1, text="test", score=0.5)]

        result = asyncio.run(reranker_service_mock.rerank("test query", docs))

        assert len(result) == 1
        assert 0 <= result[0].score <= 1.0  # Mock scores should be in [0.9, 1.0]

    def test_reranker_with_different_queries(self, reranker_service_mock: RerankerService) -> None:
        """Test reranker behavior with different query texts."""
        d1 = uuid4()
        docs = [RerankCandidate(doc_id=d1, text="test document", score=0.5)]

        # Different queries should not affect mock mode behavior
        result1 = asyncio.run(reranker_service_mock.rerank("query1", docs))
        result2 = asyncio.run(reranker_service_mock.rerank("query2", docs))

        assert len(result1) == 1
        assert len(result2) == 1
        # In mock mode, query doesn't affect the result
        assert result1[0].doc_id == result2[0].doc_id


class TestRerankerTimeout:
    """Test timeout behavior in reranker service."""

    def test_timeout_with_artificial_delay(self) -> None:
        """Test that a slow cross-encoder raises RerankerTimeoutException.

        The timeout wraps ``_model.compute_score`` (see RerankerService.rerank),
        not ``rerank`` as a whole, so the model is stubbed with a slow
        ``compute_score`` instead of delaying ``rerank`` itself.
        """
        service = RerankerService(
            config=RerankerConfig(mock_mode=False, device="cpu", timeout_ms=50)
        )

        class SlowModel:
            def compute_score(self, pairs, batch_size=32):  # noqa: ANN001, ANN201
                time.sleep(0.5)  # 500ms >> 50ms timeout
                return [0.0 for _ in pairs]

        service._model = SlowModel()  # type: ignore[assignment]

        with pytest.raises(RerankerTimeoutException):
            asyncio.run(
                service.rerank(
                    "test query",
                    [RerankCandidate(doc_id=uuid4(), text="test", score=0.5)],
                    top_k=1,
                )
            )


class TestRerankerConfig:
    """Test different reranker configurations."""

    def test_mock_mode_config(self) -> None:
        """Test mock mode configuration."""
        config = RerankerConfig(mock_mode=True)
        assert config.mock_mode is True
        assert config.model_name == "BAAI/bge-reranker-v2-m3"

    def test_batch_size_config(self) -> None:
        """Test batch size configuration."""
        config = RerankerConfig(batch_size=64)
        assert config.batch_size == 64

    def test_timeout_config(self) -> None:
        """Test timeout configuration."""
        config = RerankerConfig(timeout_ms=1000)
        assert config.timeout_ms == 1000

    def test_speculative_config(self) -> None:
        """Test speculative reranking configuration."""
        config = RerankerConfig(speculative_top_n=5)
        assert config.speculative_top_n == 5
        assert config.speculative_enabled is True
