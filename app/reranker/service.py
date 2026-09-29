"""Cross-encoder reranker service (C-01, ARCHITECT §7.3).

Implements BAAI/bge-reranker-v2-m3 with lazy loading, mock mode, sigmoid normalization,
async inference, and FastAPI DI integration. Latency target: ~5ms/pair on GPU,
~250ms for 50 pairs, p99 ≤ 350ms.
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np
import torch
from FlagEmbedding import FlagReranker

from app.config import RerankerConfig
from app.observability import metrics
from app.reranker.exceptions import RerankerTimeoutException, RerankerUnavailableException
from app.reranker.schemas import RerankCandidate, RerankResult

logger = logging.getLogger(__name__)


class RerankerService:
    """Cross-encoder reranker service with lazy loading and circuit breaker support.

    Parameters
    ----------
    config
        Reranker configuration (model, batch, timeout, mock mode).
    """

    def __init__(self, config: RerankerConfig) -> None:
        self._config = config
        self._model: Optional[FlagReranker] = None
        self._load_lock = asyncio.Lock()
        self._device: str = "cpu"  # fallback, updated in lazy_load

    async def warmup(self) -> None:
        """Load the model eagerly at application startup.

        Call this from FastAPI lifespan if RERANKER_WARMUP=true.
        """
        await self._lazy_load()

    async def rerank(
        self, query: str, docs: list[RerankCandidate], top_k: Optional[int] = None
    ) -> list[RerankResult]:
        """Rerank documents using cross-encoder (ARCHITECT §7.3).

        Parameters
        ----------
        query
            Search query text.
        docs
            Documents to rerank, with pre-fusion scores.
        top_k
            Maximum number of results to return. If None, return all.

        Returns
        -------
        list[RerankResult]
            Documents reranked by cross-encoder score, descending.

        Raises
        ------
        RerankerTimeoutException
            If inference exceeds RerankerConfig.timeout_ms.
        RerankerUnavailableException
            If model fails to load or device error.
        """
        if not docs:
            return []

        start_time = time.time()

        # Mock mode short-circuits BEFORE the lazy load: RERANKER_MOCK_MODE is the
        # CI/dev escape hatch, and loading the 2.3 GB cross-encoder would defeat it.
        if self._config.mock_mode:
            return self._mock_rerank(docs, top_k)

        await self._lazy_load()

        model = self._model
        if model is None:  # pragma: no cover - _lazy_load raises or sets it
            raise RerankerUnavailableException("Reranker model not loaded after lazy load")

        # Prepare pairs: [(query, doc_text), ...]
        pairs = [(query, doc.text) for doc in docs]

        # Async inference with timeout
        try:
            scores = await asyncio.wait_for(
                asyncio.to_thread(model.compute_score, pairs, batch_size=self._config.batch_size),
                timeout=self._config.timeout_ms / 1000.0,
            )
        except Exception as exc:
            if isinstance(exc, asyncio.TimeoutError):
                raise RerankerTimeoutException(
                    f"Rerank inference timeout: {self._config.timeout_ms}ms exceeded"
                ) from exc
            raise RerankerUnavailableException(f"Rerank inference failed: {exc}") from exc

        # Normalize raw logits to [0, 1] via sigmoid
        scores = np.array(scores, dtype=np.float32)
        normalized_scores = 1 / (1 + np.exp(-scores))

        # Create results and sort by score descending
        results = [
            RerankResult(doc_id=doc.doc_id, score=float(score))
            for doc, score in zip(docs, normalized_scores)
        ]
        results.sort(key=lambda x: -x.score)

        # Apply top_k limit
        if top_k is not None:
            results = results[:top_k]

        # Log performance metrics and record latency metric
        inference_ms = int((time.time() - start_time) * 1000) if 'start_time' in locals() else 0
        logger.info(
            "Rerank done",
            extra={
                "query_len": len(query),
                "n_docs": len(docs),
                "device": self._device,
                "inference_ms": inference_ms,
            },
        )
        
        # Record latency metric (C-12)
        metrics.reranker_latency_ms.labels(
            device=self._device, 
            mock=self._config.mock_mode
        ).observe(inference_ms)

        return results

    async def _lazy_load(self) -> None:
        """Lazy load the model on first use with thread safety."""
        if self._model is not None:
            return

        async with self._load_lock:
            if self._model is not None:
                return  # another beat us to it

            try:
                start_time = time.time()
                # Detect GPU availability
                if self._config.device == "auto":
                    self._device = "cuda" if torch.cuda.is_available() else "cpu"
                else:
                    self._device = self._config.device

                # Load model
                self._model = FlagReranker(
                    model_name_or_path=self._config.model_name,
                    device=self._device,
                    use_fp16=True if self._device == "cuda" else False,
                )

                # Validate model dimension (should match embedding_models.dimension in future)
                load_ms = int((time.time() - start_time) * 1000) if 'start_time' in locals() else 0
                if hasattr(self._model, "get_sentence_embedding_dimension"):
                    model_dim = self._model.get_sentence_embedding_dimension()
                    logger.info(
                        "Reranker model loaded",
                        extra={
                            "model": self._config.model_name,
                            "device": self._device,
                            "dim": model_dim,
                            "load_ms": load_ms,
                        },
                    )
                else:
                    logger.info(
                        "Reranker model loaded",
                        extra={
                            "model": self._config.model_name,
                            "device": self._device,
                            "load_ms": load_ms,
                        },
                    )

            except Exception as exc:
                raise RerankerUnavailableException(
                    f"Failed to load reranker model '{self._config.model_name}': {exc}"
                ) from exc

    def _mock_rerank(self, docs: list[RerankCandidate], top_k: Optional[int]) -> list[RerankResult]:
        """Mock reranking for development without GPU.

        Returns uniform scores (1.0, 0.9, 0.8, ...) or preserves input scores.
        """
        if top_k is not None:
            docs = docs[:top_k]

        # Return uniform scores by rank (1.0, 0.9, 0.8, ...)
        # Alternatively, could preserve input scores: RerankResult(doc_id=doc.doc_id, score=doc.score)
        results = [
            RerankResult(doc_id=doc.doc_id, score=1.0 - (i * 0.1))
            for i, doc in enumerate(docs)
        ]
        return results