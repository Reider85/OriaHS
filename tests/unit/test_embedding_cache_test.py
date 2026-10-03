"""Unit tests for EmbeddingCache (ARCHITECT §5.3) + fast-path helper.

Uses fakeredis — no real Redis server required.
"""

import hashlib

import numpy as np
import pytest
from fakeredis.aioredis import FakeRedis

from app.embedding.cache import (
    CONTENT_HASH_TTL_SECONDS,
    QUERY_EMBEDDING_TTL_SECONDS,
    EmbeddingCache,
    should_skip_upsert,
)

MODEL = "bge-m3-v1"


def make_vector(seed: int = 0, dim: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dim).astype(np.float32)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
async def redis() -> FakeRedis:
    client = FakeRedis()
    yield client
    await client.aclose()


@pytest.fixture
def cache(redis: FakeRedis) -> EmbeddingCache:
    return EmbeddingCache(redis)


class TestShouldSkipUpsert:
    def test_matching_hash_skips(self) -> None:
        assert should_skip_upsert("abc", "abc") is True

    def test_different_hash_does_not_skip(self) -> None:
        assert should_skip_upsert("abc", "xyz") is False

    def test_missing_existing_hash_does_not_skip(self) -> None:
        assert should_skip_upsert("abc", None) is False


class TestRoundtrip:
    async def test_content_hash_roundtrip(self, cache: EmbeddingCache) -> None:
        vector = make_vector(7)
        await cache.set_by_content_hash("h1", MODEL, vector)
        got = await cache.get_by_content_hash("h1", MODEL)
        assert got is not None
        np.testing.assert_allclose(got, vector, atol=1e-6)

    async def test_content_hash_miss_returns_none(self, cache: EmbeddingCache) -> None:
        assert await cache.get_by_content_hash("nope", MODEL) is None

    async def test_query_roundtrip(self, cache: EmbeddingCache) -> None:
        vector = make_vector(3)
        await cache.set_query_embedding("find me", MODEL, vector)
        got = await cache.get_query_embedding("find me", MODEL)
        assert got is not None
        np.testing.assert_allclose(got, vector, atol=1e-6)

    async def test_query_miss_returns_none(self, cache: EmbeddingCache) -> None:
        assert await cache.get_query_embedding("nope", MODEL) is None

    async def test_mget_mixed_hits_and_miss(self, cache: EmbeddingCache) -> None:
        v1, v2 = make_vector(1), make_vector(2)
        await cache.set_by_content_hash("h1", MODEL, v1)
        await cache.set_by_content_hash("h2", MODEL, v2)
        result = await cache.mget_by_content_hash([("h1", MODEL), ("h2", MODEL), ("h3", MODEL)])
        assert result[0] is not None
        assert result[1] is not None
        assert result[2] is None
        np.testing.assert_allclose(result[0], v1, atol=1e-6)
        np.testing.assert_allclose(result[1], v2, atol=1e-6)

    async def test_mget_empty(self, cache: EmbeddingCache) -> None:
        assert await cache.mget_by_content_hash([]) == []


class TestTtl:
    async def test_content_hash_ttl(self, redis: FakeRedis, cache: EmbeddingCache) -> None:
        await cache.set_by_content_hash("h1", MODEL, make_vector(1))
        ttl = await redis.ttl(f"hash:h1:{MODEL}")
        assert ttl == pytest.approx(CONTENT_HASH_TTL_SECONDS, abs=5)

    async def test_query_ttl(self, redis: FakeRedis, cache: EmbeddingCache) -> None:
        await cache.set_query_embedding("query text", MODEL, make_vector(2))
        key = f"qemb:{MODEL}:{sha256_hex('query text')}"
        ttl = await redis.ttl(key)
        assert ttl == pytest.approx(QUERY_EMBEDDING_TTL_SECONDS, abs=5)


class TestKeys:
    async def test_content_hash_key_format(self, redis: FakeRedis, cache: EmbeddingCache) -> None:
        await cache.set_by_content_hash("deadbeef", MODEL, make_vector(1))
        keys = [key.decode() for key in await redis.keys("hash:*")]
        assert keys == [f"hash:deadbeef:{MODEL}"]

    async def test_query_key_format(self, redis: FakeRedis, cache: EmbeddingCache) -> None:
        await cache.set_query_embedding("hello", MODEL, make_vector(1))
        keys = [key.decode() for key in await redis.keys("qemb:*")]
        assert keys == [f"qemb:{MODEL}:{sha256_hex('hello')}"]

    async def test_model_name_part_of_key(self, redis: FakeRedis, cache: EmbeddingCache) -> None:
        await cache.set_by_content_hash("h1", "model-a", make_vector(1))
        assert await cache.get_by_content_hash("h1", "model-b") is None
        keys = [key.decode() for key in await redis.keys("hash:*")]
        assert keys == ["hash:h1:model-a"]


class TestComputeWithCache:
    async def test_fetches_only_missing(self, cache: EmbeddingCache) -> None:
        hash_a = sha256_hex("a")
        await cache.set_by_content_hash(hash_a, MODEL, make_vector(1))
        calls: list[list[str]] = []

        async def compute_fn(texts: list[str]) -> np.ndarray:
            calls.append(texts)
            return np.stack([make_vector(100 + i) for i in range(len(texts))])

        result = await cache.compute_with_cache(["a", "b", "c"], MODEL, compute_fn)
        assert len(result) == 3
        assert calls == [["b", "c"]]
        np.testing.assert_allclose(result[0], make_vector(1), atol=1e-6)

    async def test_missing_results_stored_back(self, cache: EmbeddingCache) -> None:
        async def compute_fn(texts: list[str]) -> np.ndarray:
            return np.stack([make_vector(50 + i) for i in range(len(texts))])

        await cache.compute_with_cache(["only"], MODEL, compute_fn)
        got = await cache.get_by_content_hash(sha256_hex("only"), MODEL)
        assert got is not None
        np.testing.assert_allclose(got, make_vector(50), atol=1e-6)

    async def test_second_call_does_not_recompute(self, cache: EmbeddingCache) -> None:
        calls: list[list[str]] = []

        async def compute_fn(texts: list[str]) -> np.ndarray:
            calls.append(texts)
            return np.stack([make_vector(200 + i) for i in range(len(texts))])

        first = await cache.compute_with_cache(["x", "y"], MODEL, compute_fn)
        assert len(first) == 2
        second = await cache.compute_with_cache(["x", "y"], MODEL, compute_fn)
        assert len(second) == 2
        assert calls == [["x", "y"]]

    async def test_empty_texts(self, cache: EmbeddingCache) -> None:
        async def compute_fn(texts: list[str]) -> np.ndarray:
            raise AssertionError("compute_fn must not be called")

        assert await cache.compute_with_cache([], MODEL, compute_fn) == []
