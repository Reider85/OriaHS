"""Unit tests for EmbeddingService (ARCHITECT §5.1, §5.2).

Tests hit a real sentence-transformers model, so they are marked slow
and force CPU via config so they run anywhere without a GPU.
"""

import numpy as np
import pytest

from app.config import EmbeddingConfig
from app.embedding.service import EXPECTED_DIMENSION, EmbeddingService


@pytest.fixture
def service() -> EmbeddingService:
    return EmbeddingService(config=EmbeddingConfig(device="cpu"))


@pytest.mark.slow
class TestEmbedTexts:
    async def test_shape_and_dtype(self, service: EmbeddingService) -> None:
        result = await service.embed_texts(["hello", "world"])
        assert result.shape == (2, EXPECTED_DIMENSION)
        assert result.dtype == np.float32

    async def test_l2_normalized(self, service: EmbeddingService) -> None:
        result = await service.embed_texts(["hello"])
        np.testing.assert_allclose(
            np.linalg.norm(result, axis=1), 1.0, atol=1e-5
        )

    async def test_deterministic(self, service: EmbeddingService) -> None:
        a = await service.embed_texts(["test sentence"])
        b = await service.embed_texts(["test sentence"])
        np.testing.assert_allclose(a, b, atol=1e-6)

    async def test_empty_input(self, service: EmbeddingService) -> None:
        result = await service.embed_texts([])
        assert result.shape == (0, EXPECTED_DIMENSION)
        assert result.dtype == np.float32

    async def test_batch_split_32_plus_1(self, service: EmbeddingService) -> None:
        texts = [f"text_{i}" for i in range(33)]
        result = await service.embed_texts(texts)
        assert result.shape == (33, EXPECTED_DIMENSION)


@pytest.mark.slow
class TestEmbedQuery:
    async def test_shape_and_dtype(self, service: EmbeddingService) -> None:
        result = await service.embed_query("hello world")
        assert result.shape == (EXPECTED_DIMENSION,)
        assert result.dtype == np.float32

    async def test_l2_normalized(self, service: EmbeddingService) -> None:
        result = await service.embed_query("hello world")
        np.testing.assert_allclose(np.linalg.norm(result), 1.0, atol=1e-5)


@pytest.mark.slow
class TestConfig:
    def test_fields_present(self) -> None:
        config = EmbeddingConfig()
        assert config.model_name == "bge-m3-v1"
        assert config.batch_size == 32
        assert config.max_length == 512
        assert config.cache_ttl_seconds == 2592000
        assert config.device == "auto"

    async def test_cpu_fallback(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
        service = EmbeddingService(config=EmbeddingConfig(device="auto"))
        with caplog.at_level("WARNING", logger="app.embedding.service"):
            result = await service.embed_texts(["test"])
        assert result.shape == (1, EXPECTED_DIMENSION)
        assert service._device == "cpu"  # noqa: SLF001
        assert any(
            "CUDA not available" in r.message for r in caplog.records
        )
