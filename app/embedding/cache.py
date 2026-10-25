"""Embedding cache (ARCHITECT §5.3) + fast-path helper (TRIZ-gate).

Two Redis caches live here:

- ``hash:{sha256(content)}:{model_name}`` — document content-hash cache,
  TTL 30 days. Reuses embeddings across re-indexes.
- ``qemb:{model_name}:{sha256(query)}`` — query-embedding cache, TTL 1 hour.

``model_name`` is always part of the key (ARCHITECT §5.7 fix) so switching
embedding models never serves stale vectors. Serialization is plain
``np.float32.tobytes()`` — never pickle.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable

import numpy as np
import redis.asyncio as aioredis

from app.config import settings
from app.observability import metrics
from app.observability.tracing import get_tracer, span

tracer = get_tracer("embedding.cache")

CONTENT_HASH_TTL_SECONDS = 2592000  # 30 days
QUERY_EMBEDDING_TTL_SECONDS = 3600  # 1 hour

VECTOR_DTYPE = np.float32


def _serialize(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=VECTOR_DTYPE).tobytes()


def _deserialize(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=VECTOR_DTYPE)


def text_sha256(text: str) -> str:
    """Generate SHA256 hash for text (used for negative cache keys)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def should_skip_upsert(content_hash: str, existing_hash: str | None) -> bool:
    """Fast-path: skip Qdrant upsert when the stored hash already matches.

    Saves ~3 ms per operation on re-index (ARCHITECT §4.3, ROADMAP §3.3).
    """
    return existing_hash == content_hash


class EmbeddingCache:
    """Async Redis-backed embedding store (content-hash + query embedding)."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    @staticmethod
    def _content_hash_key(content_hash: str, model_name: str) -> str:
        return f"hash:{content_hash}:{model_name}"

    @staticmethod
    def _query_key(query: str, model_name: str) -> str:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return f"qemb:{model_name}:{digest}"

    @staticmethod
    def _negative_key(text: str, model_name: str) -> str:
        """Generate negative cache key for text (ARCHITECT §5.3)."""
        digest = text_sha256(text)
        return f"neg:{model_name}:{digest}"

    async def get_by_content_hash(self, content_hash: str, model_name: str) -> np.ndarray | None:
        metrics.embedding_cache_requests_total.inc()
        raw = await self._redis.get(self._content_hash_key(content_hash, model_name))
        if raw is None:
            return None
        metrics.embedding_cache_hits_total.inc()
        assert isinstance(raw, bytes)
        return _deserialize(raw)

    async def set_by_content_hash(
        self, content_hash: str, model_name: str, vector: np.ndarray
    ) -> None:
        await self._redis.set(
            self._content_hash_key(content_hash, model_name),
            _serialize(vector),
            ex=CONTENT_HASH_TTL_SECONDS,
        )

    @span(tracer, "redis.cache_lookup", attributes={
        "type": "content_hash",
        "batch_size": lambda: len(items),
    })
    async def mget_by_content_hash(self, items: list[tuple[str, str]]) -> list[np.ndarray | None]:
        """Batch lookup; one round-trip. Returns ``None`` for cache misses."""
        if not items:
            return []
        keys = [self._content_hash_key(hash_, model) for hash_, model in items]
        metrics.embedding_cache_requests_total.inc(len(items))
        raw_values = await self._redis.mget(keys)
        result: list[np.ndarray | None] = []
        for raw in raw_values:
            if raw is None:
                result.append(None)
            else:
                assert isinstance(raw, bytes)
                result.append(_deserialize(raw))
        hits = sum(1 for v in result if v is not None)
        metrics.embedding_cache_hits_total.inc(hits)
        return result

    @span(tracer, "redis.cache_lookup", attributes={
        "type": "query_embedding",
        "model_name": lambda: model_name,
    })
    async def get_query_embedding(self, query: str, model_name: str) -> np.ndarray | None:
        metrics.embedding_cache_requests_total.inc()
        raw = await self._redis.get(self._query_key(query, model_name))
        if raw is None:
            return None
        metrics.embedding_cache_hits_total.inc()
        assert isinstance(raw, bytes)
        return _deserialize(raw)

    async def set_query_embedding(self, query: str, model_name: str, vector: np.ndarray) -> None:
        await self._redis.set(
            self._query_key(query, model_name),
            _serialize(vector),
            ex=QUERY_EMBEDDING_TTL_SECONDS,
        )

    async def compute_with_cache(
        self,
        texts: list[str],
        model_name: str,
        compute_fn: Callable[[list[str]], Awaitable[np.ndarray]],
    ) -> list[np.ndarray]:
        """cache-lookup → compute (misses only) → cache-store, in one batch."""
        if not texts:
            return []
        
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        cached = await self.mget_by_content_hash([(hash_, model_name) for hash_ in hashes])
        results: list[np.ndarray | None] = list(cached)
        missing_idx = [i for i, value in enumerate(results) if value is None]
        
        if missing_idx:
            missing_texts = [texts[i] for i in missing_idx]
            
            # Check negative cache for missing items
            negative_hits = await self._mget_negative_cache(
                [(text, model_name) for text in missing_texts]
            )
            
            # Separate negative hits from valid computations
            valid_missing_idx = []
            valid_missing_texts = []
            negative_indices = []
            
            for j, idx in enumerate(missing_idx):
                if negative_hits[j]:
                    # Negative cache hit - mark as None and set negative cache (if not already set)
                    results[idx] = None
                    negative_indices.append(idx)
                else:
                    # No negative cache hit - need to compute
                    valid_missing_idx.append(idx)
                    valid_missing_texts.append(missing_texts[j])
            
            # Set negative cache for items that were already negative hits (refresh TTL)
            if negative_indices:
                negative_items = [
                    (texts[i], model_name) for i in negative_indices
                ]
                await self._set_negative_cache_many(negative_items)
            
            # Compute only valid items
            if valid_missing_texts:
                computed = np.asarray(await compute_fn(valid_missing_texts), dtype=VECTOR_DTYPE)
                if computed.ndim == 1:
                    computed = computed.reshape(1, -1)
                if computed.shape[0] != len(valid_missing_idx):
                    raise ValueError(
                        f"compute_fn returned {computed.shape[0]} vectors for {len(valid_missing_idx)} texts"
                    )
                
                # Validate computed vectors and cache/store valid ones
                async with self._redis.pipeline(transaction=False) as pipe:
                    for j, idx in enumerate(valid_missing_idx):
                        vector = computed[j]
                        # Check for NaN/Inf vectors (invalid)
                        if not np.isfinite(vector).all():
                            # Set negative cache for invalid vector
                            await self.set_negative_cache(texts[idx], model_name)
                            results[idx] = None
                        else:
                            # Cache valid vector
                            pipe.set(
                                self._content_hash_key(hashes[idx], model_name),
                                _serialize(vector),
                                ex=CONTENT_HASH_TTL_SECONDS,
                            )
                            results[idx] = vector
                    await pipe.execute()
        
        return [result for result in results if result is not None]

    async def get_negative_cache(self, text: str, model_name: str) -> bool:
        """Check if text is cached as negative (broken/invalid).
        
        Returns True if negative cache hit, False if miss or disabled.
        """
        if not settings.feature_flags.negative_cache_enabled:
            return False
        
        key = self._negative_key(text, model_name)
        raw = await self._redis.get(key)
        if raw is not None:
            metrics.record_negative_cache_event(hit=True)
            return True
        metrics.record_negative_cache_event(hit=False)
        return False

    async def set_negative_cache(self, text: str, model_name: str) -> None:
        """Cache text as negative (broken/invalid) with TTL.
        
        TTL includes jitter (±10s) to avoid thundering herd.
        """
        if not settings.feature_flags.negative_cache_enabled:
            return
        
        key = self._negative_key(text, model_name)
        ttl = settings.negative_cache.null_ttl_seconds
        jitter = secrets.randbelow(21) - 10  # -10 to +10 seconds
        final_ttl = max(1, ttl + jitter)  # Ensure positive TTL
        
        await self._redis.set(key, "null", ex=final_ttl)
        metrics.negative_cache_sets_total.inc()

    async def clear_negative_cache(self, text: str, model_name: str) -> None:
        """Remove negative cache entry for text (if any)."""
        key = self._negative_key(text, model_name)
        await self._redis.delete(key)

    async def _mget_negative_cache(
        self, items: list[tuple[str, str]]
    ) -> list[bool]:
        """Batch negative cache lookup; one round-trip.
        
        Returns True for negative cache hits, False for misses.
        """
        if not settings.feature_flags.negative_cache_enabled:
            return [False] * len(items)
        
        keys = [self._negative_key(text, model) for text, model in items]
        raw_values = await self._redis.mget(keys)
        
        hits = 0
        results: list[bool] = []
        for raw in raw_values:
            if raw is not None:
                results.append(True)
                hits += 1
            else:
                results.append(False)
        
        for hit in results:
            metrics.record_negative_cache_event(hit=hit)
        
        return results

    async def _set_negative_cache_many(
        self, items: list[tuple[str, str]]
    ) -> None:
        """Batch negative cache set; one round-trip."""
        if not settings.feature_flags.negative_cache_enabled:
            return
        
        async with self._redis.pipeline(transaction=False) as pipe:
            for text, model_name in items:
                key = self._negative_key(text, model_name)
                ttl = settings.negative_cache.null_ttl_seconds
                jitter = secrets.randbelow(21) - 10  # -10 to +10 seconds
                final_ttl = max(1, ttl + jitter)
                pipe.set(key, "null", ex=final_ttl)
                metrics.negative_cache_sets_total.inc()
            await pipe.execute()
