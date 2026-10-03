"""Embedding Service — BAAI/bge-m3 lazy loader + batch encoder.

ARCHITECT §5.1 (model selection), §5.2 (batching), ROADMAP §3.2.3.
"""

import asyncio
import logging
import time

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import EmbeddingConfig
from app.embedding.cache import EmbeddingCache

logger = logging.getLogger(__name__)

EXPECTED_DIMENSION = 1024


class EmbeddingService:
    """Async wrapper around sentence-transformers BAAI/bge-m3.

    Model is loaded lazily on first embed_* call and protected by an
    asyncio.Lock (loading is not thread-safe). Inference runs in a thread
    pool so the event loop is never blocked.
    """

    def __init__(
        self, config: EmbeddingConfig | None = None, cache: EmbeddingCache | None = None
    ) -> None:
        self._config = config or EmbeddingConfig()
        self._cache = cache
        self._model: SentenceTransformer | None = None
        self._load_lock = asyncio.Lock()
        self._device: str | None = None

    def _resolve_device(self) -> str:
        if self._config.device != "auto":
            return self._config.device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
        return "cpu"

    def _load_model(self) -> SentenceTransformer:
        device = self._resolve_device()
        self._device = device
        if device == "cpu":
            logger.warning("CUDA not available, running embedding model on CPU (slow)")
        t0 = time.perf_counter()
        model = SentenceTransformer("BAAI/bge-m3", device=device)
        model.max_seq_length = self._config.max_length
        get_dimension = (
            getattr(model, "get_embedding_dimension", None)
            or model.get_sentence_embedding_dimension
        )
        dim = get_dimension()
        load_ms = (time.perf_counter() - t0) * 1000
        if dim != EXPECTED_DIMENSION:
            raise RuntimeError(
                f"Model dimension mismatch: expected {EXPECTED_DIMENSION}, got {dim}"
            )
        logger.info(
            "Embedding model loaded",
            extra={
                "model": self._config.model_name,
                "device": device,
                "dim": dim,
                "load_ms": round(load_ms, 1),
            },
        )
        return model

    async def _ensure_model(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            self._model = await asyncio.to_thread(self._load_model)
            return self._model

    async def _encode(self, texts: list[str]) -> np.ndarray:
        """Run the model on a list of texts (async-safe, event-loop friendly)."""
        model = await self._ensure_model()
        embeddings = await asyncio.to_thread(
            model.encode,
            texts,
            batch_size=self._config.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(embeddings, dtype=np.float32)

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Encode a list of texts into normalized float32 embeddings."""
        if not texts:
            return np.empty((0, EXPECTED_DIMENSION), dtype=np.float32)
        if self._cache is None:
            return await self._encode(texts)
        vectors = await self._cache.compute_with_cache(texts, self._config.model_name, self._encode)
        return np.asarray(vectors, dtype=np.float32)

    async def embed_query(self, query: str) -> np.ndarray:
        """Encode a single query string (used by search channels)."""
        if self._cache is not None:
            cached = await self._cache.get_query_embedding(query, self._config.model_name)
            if cached is not None:
                return cached
        result = await self.embed_texts([query])
        vector: np.ndarray = result[0]
        if self._cache is not None:
            await self._cache.set_query_embedding(query, self._config.model_name, vector)
        return vector
