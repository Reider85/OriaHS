"""Embedding cache (ARCHITECT §5.3) + fast-path helper (TRIZ-gate).

Two Redis caches live here:

- ``hash:{sha256(content)}:{model_name}`` — document content-hash cache,
  TTL 30 days. Reuses embeddings across re-indexes.
- ``qemb:{model_name}:{sha256(query)}`` — query-embedding cache, TTL 1 hour.

``model_name`` is always part of the key (ARCHITECT §5.7 fix) so switching
embedding models never serves stale vectors. Serialization is plain
``np.float32.tobytes()`` — never pickle.
"""

import hashlib
from collections.abc import Awaitable, Callable

import numpy as np
import redis.asyncio as aioredis

CONTENT_HASH_TTL_SECONDS = 2592000  # 30 days
QUERY_EMBEDDING_TTL_SECONDS = 3600  # 1 hour

VECTOR_DTYPE = np.float32


def _serialize(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=VECTOR_DTYPE).tobytes()


def _deserialize(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=VECTOR_DTYPE)


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

    async def get_by_content_hash(
        self, content_hash: str, model_name: str
    ) -> np.ndarray | None:
        raw = await self._redis.get(self._content_hash_key(content_hash, model_name))
        if raw is None:
            return None
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

    async def mget_by_content_hash(
        self, items: list[tuple[str, str]]
    ) -> list[np.ndarray | None]:
        """Batch lookup; one round-trip. Returns ``None`` for cache misses."""
        if not items:
            return []
        keys = [self._content_hash_key(hash_, model) for hash_, model in items]
        raw_values = await self._redis.mget(keys)
        result: list[np.ndarray | None] = []
        for raw in raw_values:
            if raw is None:
                result.append(None)
            else:
                assert isinstance(raw, bytes)
                result.append(_deserialize(raw))
        return result

    async def get_query_embedding(self, query: str, model_name: str) -> np.ndarray | None:
        raw = await self._redis.get(self._query_key(query, model_name))
        if raw is None:
            return None
        assert isinstance(raw, bytes)
        return _deserialize(raw)

    async def set_query_embedding(
        self, query: str, model_name: str, vector: np.ndarray
    ) -> None:
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
        hashes = [
            hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts
        ]
        cached = await self.mget_by_content_hash(
            [(hash_, model_name) for hash_ in hashes]
        )
        results: list[np.ndarray | None] = list(cached)
        missing_idx = [i for i, value in enumerate(results) if value is None]
        if missing_idx:
            missing_texts = [texts[i] for i in missing_idx]
            computed = np.asarray(await compute_fn(missing_texts), dtype=VECTOR_DTYPE)
            if computed.ndim == 1:
                computed = computed.reshape(1, -1)
            if computed.shape[0] != len(missing_idx):
                raise ValueError(
                    "compute_fn returned "
                    f"{computed.shape[0]} vectors for {len(missing_idx)} texts"
                )
            async with self._redis.pipeline(transaction=False) as pipe:
                for j, index in enumerate(missing_idx):
                    pipe.set(
                        self._content_hash_key(hashes[index], model_name),
                        _serialize(computed[j]),
                        ex=CONTENT_HASH_TTL_SECONDS,
                    )
                await pipe.execute()
            for j, index in enumerate(missing_idx):
                results[index] = computed[j]
        return [result for result in results if result is not None]
