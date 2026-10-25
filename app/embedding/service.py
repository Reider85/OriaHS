"""Embedding Service — BAAI/bge-m3 lazy loader + batch encoder.

ARCHITECT §5.1 (model selection), §5.2 (batching), ROADMAP §3.2.3.
"""

import asyncio
import logging
import time
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import EmbeddingConfig
from app.embedding.cache import EmbeddingCache
from app.embedding.exceptions import EmbeddingSkippedError
from app.observability.tracing import get_tracer, span
from app.utils.normalization import normalize_nfc

logger = logging.getLogger(__name__)
tracer = get_tracer("embedding.service")

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

        # Use the HF model ID from config, default to model name
        hf_model = self._config.hf_model or self._config.model_name
        model = SentenceTransformer(hf_model, device=device)
        model.max_seq_length = self._config.max_length

        get_dimension = (
            getattr(model, "get_embedding_dimension", None)
            or model.get_sentence_embedding_dimension
        )
        dim = get_dimension()
        load_ms = (time.perf_counter() - t0) * 1000

        # Check expected dimension if configured
        expected_dim = self._config.expected_dimension or EXPECTED_DIMENSION
        if dim != expected_dim:
            raise RuntimeError(
                f"Model dimension mismatch: expected {expected_dim}, got {dim}"
            )

        logger.info(
            "Embedding model loaded",
            extra={
                "model": self._config.model_name,
                "hf_model": hf_model,
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
        """Encode a list of texts into normalized float32 embeddings.
        
        Raises EmbeddingSkippedError if computation returns partial results
        (fewer vectors than requested texts).
        """
        if not texts:
            expected_dim = self._config.expected_dimension or EXPECTED_DIMENSION
            return np.empty((0, expected_dim), dtype=np.float32)
        
        if self._cache is None:
            return await self._encode(texts)
        
        vectors = await self._cache.compute_with_cache(texts, self._config.model_name, self._encode)
        
        # Ensure we got the expected number of vectors
        if len(vectors) != len(texts):
            raise EmbeddingSkippedError(
                f"embedding skipped for {len(texts) - len(vectors)}/{len(texts)} texts "
                "(negative cache or invalid model output)"
            )
        
        return np.asarray(vectors, dtype=np.float32)

    @span(tracer, "embedding.query", attributes={
        "cache_hit": lambda: cached is not None,
        "model_name": lambda: self._config.model_name,
    })
    async def embed_query(self, query: str) -> np.ndarray | None:
        """Encode a single query string (used by search channels).
        
        Returns None if query is cached as negative or exceeds length limit.
        """
        # Apply NFC normalization before cache lookup for consistency
        normalized_query = normalize_nfc(query)

        # Check negative cache first
        if self._cache is not None:
            if await self._cache.get_negative_cache(normalized_query, self._config.model_name):
                return None

        # Check length limit (only for queries, not documents)
        max_length = self._config.max_length or 512
        if len(normalized_query) > max_length * 2:
            if self._cache is not None:
                await self._cache.set_negative_cache(normalized_query, self._config.model_name)
            return None

        cached = None
        if self._cache is not None:
            cached = await self._cache.get_query_embedding(normalized_query, self._config.model_name)
            if cached is not None:
                return cached
        
        try:
            result = await self.embed_texts([normalized_query])
            vector: np.ndarray = result[0]
            if self._cache is not None:
                await self._cache.set_query_embedding(normalized_query, self._config.model_name, vector)
            return vector
        except EmbeddingSkippedError:
            # embed_texts was skipped due to negative cache or partial results
            return None

    @span(tracer, "embedding.document", attributes={
        "strategy": lambda: embedded.strategy,
        "chunks_count": lambda: len(embedded.chunks),
    })
    async def embed_document(
        self, 
        title: str, 
        content: str, 
        attributes: dict[str, Any] | None = None
    ) -> "EmbeddedDocument":
        """Embed a document using asymmetric chunking strategy.
        
        Args:
            title: Document title
            content: Document content
            attributes: Document attributes (may contain abstract)
            
        Returns:
            EmbeddedDocument with strategy and chunk embeddings
        """
        from app.embedding.chunking import ChunkingService
        from app.observability import metrics
        
        # Create chunking service with lazy tokenizer
        chunking = ChunkingService(tokenizer=None)  # Uses default lazy tokenizer
        
        # Plan chunking strategy
        plan = chunking.plan(title, content, attributes or {})
        
        # Embed all texts (batched, cache-aware)
        try:
            vectors = await self.embed_texts(plan.embed_texts)
        except EmbeddingSkippedError:
            # Document embedding was skipped due to negative cache or partial results
            raise RuntimeError(
                f"document embedding skipped for {len(plan.embed_texts)} texts "
                "(negative cache or invalid model output)"
            )
        
        # Apply weighted average if needed
        if plan.strategy == "weighted" and plan.weights is not None:
            # Map embeddings to section names
            section_names = ["title"]  # Always present
            if attributes and "abstract" in attributes:
                section_names.append("abstract")
            section_names.append("body")
            
            # Build section embeddings dict
            section_embeddings = {}
            for i, name in enumerate(section_names):
                if i < len(vectors):
                    section_embeddings[name] = vectors[i]
            
            # Compute weighted average
            vector = chunking.weighted_average(section_embeddings, {
                name: w for name, w in zip(section_names, plan.weights)
                if w > 0
            })
            
            # Replace all vectors with the single weighted result
            vectors = [vector] * len(plan.chunks)  # One per chunk for consistency
        
        # Emit metrics
        metrics.asymmetric_indexing_docs_total.labels(chunking_type=plan.strategy).inc()
        for chunk in plan.chunks:
            section_type = chunk.section_type or "none"
            metrics.asymmetric_indexing_chunks_total.labels(section_type=section_type).inc()
        
        # Build result
        from dataclasses import dataclass
        @dataclass
        class EmbeddedChunk:
            chunk: "DocumentChunk"
            vector: np.ndarray
        
        @dataclass
        class EmbeddedDocument:
            strategy: str
            chunks: list[EmbeddedChunk]
        
        embedded = EmbeddedDocument(
            strategy=plan.strategy,
            chunks=[
                EmbeddedChunk(chunk=chunk, vector=vector)
                for chunk, vector in zip(plan.chunks, vectors)
            ]
        )
        
        return embedded
