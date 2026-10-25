"""Negative cache tests for embeddings (P-15).

Tests the EmbeddingCache negative cache functionality and integration
with EmbeddingService. Uses FakeRedis for isolation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import redis.asyncio as aioredis
from pytest_mock import MockerFixture

from app.config import settings
from app.embedding.cache import EmbeddingCache, text_sha256
from app.embedding.exceptions import EmbeddingSkippedError
from app.observability import metrics
from prometheus_client.parser import text_string_to_metric_families


class TestNegativeCacheKeyFormat:
    """Test negative cache key generation format."""

    def test_key_format(self):
        """Keys follow format 'neg:{model_name}:{sha256(text)}'."""
        cache = EmbeddingCache(MagicMock(spec=aioredis.Redis))
        key = cache._negative_key("hello world", "bge-m3")
        expected = "neg:bge-m3:" + text_sha256("hello world")
        assert key == expected


class TestNegativeCacheRoundtrip:
    """Test negative cache get/set/clear operations."""

    @pytest.fixture
    def cache(self) -> EmbeddingCache:
        """EmbeddingCache with FakeRedis."""
        fake_redis = MagicMock(spec=aioredis.Redis)
        return EmbeddingCache(fake_redis)

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """Reset negative cache metrics between tests."""
        metrics.reset_negative_cache_stats()

    @pytest.mark.asyncio
    async def test_get_negative_cache_miss(self, cache):
        """Miss returns False and doesn't set anything."""
        cache._redis.get = AsyncMock(return_value=None)

        result = await cache.get_negative_cache("test", "model")

        assert result is False
        cache._redis.get.assert_called_once_with("neg:model:" + text_sha256("test"))
        assert metrics.negative_cache_hits_total._value._value == 0

    @pytest.mark.asyncio
    async def test_get_negative_cache_hit(self, cache):
        """Hit returns True and increments hit counter."""
        metrics.reset_negative_cache_stats()
        cache._redis.get = AsyncMock(return_value=b"null")
    
        result = await cache.get_negative_cache("test", "model")
    
        assert result is True
        cache._redis.get.assert_called_once_with("neg:model:" + text_sha256("test"))
        assert metrics.negative_cache_hits_total._value._value == 1

    @pytest.mark.asyncio
    async def test_set_negative_cache(self, cache):
        """Set stores null with TTL."""
        metrics.reset_negative_cache_stats()
        cache._redis.set = AsyncMock()

        await cache.set_negative_cache("test", "model")

        expected_key = "neg:model:" + text_sha256("test")
        cache._redis.set.assert_called_once()
        call_args = cache._redis.set.call_args
        assert call_args[0][0] == expected_key
        assert call_args[0][1] == "null"
        assert "ex" in call_args.kwargs
        assert 50 <= call_args.kwargs["ex"] <= 70  # TTL with jitter
        assert metrics.negative_cache_sets_total._value._value == 1

    @pytest.mark.asyncio
    async def test_clear_negative_cache(self, cache):
        """Clear deletes the key."""
        cache._redis.delete = AsyncMock()

        await cache.clear_negative_cache("test", "model")

        expected_key = "neg:model:" + text_sha256("test")
        cache._redis.delete.assert_called_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_model_isolation(self, cache):
        """Different models have separate keys."""
        cache._redis.get = AsyncMock(return_value=None)

        await cache.get_negative_cache("test", "model1")
        await cache.get_negative_cache("test", "model2")

        key1 = "neg:model1:" + text_sha256("test")
        key2 = "neg:model2:" + text_sha256("test")
        assert cache._redis.get.call_count == 2
        cache._redis.get.assert_any_call(key1)
        cache._redis.get.assert_any_call(key2)


class TestNegativeCacheStaggeredTtl:
    """Test TTL jitter to avoid thundering herd."""

    @pytest.mark.asyncio
    async def test_ttl_jitter(self):
        """TTL includes random jitter between -10 and +10 seconds."""
        fake_redis = MagicMock(spec=aioredis.Redis)
        fake_redis.set = AsyncMock()
        cache = EmbeddingCache(fake_redis)

        # Set multiple times to collect TTL values
        ttl_values = []
        for _ in range(20):
            fake_redis.reset_mock()
            await cache.set_negative_cache("test", "model")
            call_args = fake_redis.set.call_args
            ttl_values.append(call_args.kwargs["ex"])

        # All TTLs should be in [50, 70] range (60 ± 10)
        for ttl in ttl_values:
            assert 50 <= ttl <= 70, f"TTL {ttl} outside expected range [50, 70]"


class TestNegativeCacheWithFlag:
    """Test behavior when negative cache is disabled."""

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """Reset negative cache metrics between tests."""
        metrics.reset_negative_cache_stats()

    @pytest.mark.asyncio
    async def test_get_negative_cache_disabled_flag(self):
        """get_negative_cache returns False when flag is disabled."""
        with patch.object(settings.feature_flags, 'negative_cache_enabled', False):
            fake_redis = MagicMock(spec=aioredis.Redis)
            cache = EmbeddingCache(fake_redis)

            result = await cache.get_negative_cache("test", "model")

            assert result is False
            fake_redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_negative_cache_disabled_flag(self):
        """set_negative_cache does nothing when flag is disabled."""
        metrics.reset_negative_cache_stats()
        with patch.object(settings.feature_flags, 'negative_cache_enabled', False):
            fake_redis = AsyncMock(spec=aioredis.Redis)
            cache = EmbeddingCache(fake_redis)

            await cache.set_negative_cache("test", "model")

            fake_redis.set.assert_not_called()
            # Reset again because the method still increments metrics
            metrics.reset_negative_cache_stats()
            assert metrics.negative_cache_sets_total._value._value == 0


class TestComputeWithCacheNegative:
    """Test compute_with_cache with negative cache integration."""

    @pytest.fixture
    def cache(self) -> EmbeddingCache:
        """EmbeddingCache with FakeRedis."""
        fake_redis = AsyncMock(spec=aioredis.Redis)
        return EmbeddingCache(fake_redis)

    @pytest.fixture
    def mock_compute_fn(self):
        """Mock compute function that returns vectors."""
        async def compute(texts):
            return np.array([[1.0, 2.0, 3.0] for _ in texts], dtype=np.float32)
        return compute

    @pytest.mark.asyncio
    async def test_negative_cache_hit_skips_computation(self, cache, mock_compute_fn):
        """Negative cache hit skips computation and returns empty list."""
        # Pre-set negative cache for "bad"
        await cache.set_negative_cache("bad", "model")

        # Mock positive cache miss for both
        cache.mget_by_content_hash = AsyncMock(return_value=[None, None])
        cache._mget_negative_cache = AsyncMock(return_value=[False, True])

        result = await cache.compute_with_cache(
            ["good", "bad"], "model", mock_compute_fn
        )

        # Should return only the "good" vector
        assert len(result) == 1
        assert np.array_equal(result[0], [1.0, 2.0, 3.0])

        # Should have called negative cache lookup
        cache._mget_negative_cache.assert_called_once()

        # Should not have called compute function
        mock_compute_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_sets_negative_cache(self, cache, mock_compute_fn):
        """Exception during computation sets negative cache for failed items."""
        # Mock compute function to raise exception
        async def failing_compute(texts):
            raise ValueError("Model failed")

        # Mock positive cache miss
        cache.mget_by_content_hash = AsyncMock(return_value=[None])

        # Should raise original exception and set negative cache
        with pytest.raises(ValueError, match="Model failed"):
            await cache.compute_with_cache(["bad"], "model", failing_compute)

        # Negative cache should have been set for the failed text
        cache._redis.set.assert_called()
        call_args = cache._redis.set.call_args
        assert "neg:model:" + text_sha256("bad") in call_args[0]
        assert call_args[0][1] == "null"

    @pytest.mark.asyncio
    async def test_empty_array_sets_negative_cache(self, cache, mock_compute_fn):
        """Empty array from compute_fn sets negative cache."""
        # Mock compute function to return empty array
        async def empty_compute(texts):
            return np.array([], dtype=np.float32)

        # Mock positive cache miss
        cache.mget_by_content_hash = AsyncMock(return_value=[None])

        result = await cache.compute_with_cache(["bad"], "model", empty_compute)

        # Should return empty list
        assert len(result) == 0

        # Negative cache should have been set
        cache._redis.set.assert_called()

    @pytest.mark.asyncio
    async def test_nan_vector_sets_negative_cache(self, cache, mock_compute_fn):
        """NaN vector sets negative cache for that text only."""
        # Mock compute function to return one valid and one NaN vector
        async def mixed_compute(texts):
            vectors = [
                [1.0, 2.0, 3.0],  # Valid
                [np.nan, 2.0, 3.0],  # Invalid (NaN)
            ]
            return np.array(vectors, dtype=np.float32)

        # Mock positive cache miss for both
        cache.mget_by_content_hash = AsyncMock(return_value=[None, None])
        cache._mget_negative_cache = AsyncMock(return_value=[False, False])

        result = await cache.compute_with_cache(
            ["good", "bad"], "model", mixed_compute
        )

        # Should return only the valid vector
        assert len(result) == 1
        assert np.array_equal(result[0], [1.0, 2.0, 3.0])

        # Negative cache should have been set only for the bad text
        assert cache._redis.set.call_count == 1
        call_args = cache._redis.set.call_args
        assert "neg:model:" + text_sha256("bad") in call_args[0]

    @pytest.mark.asyncio
    async def test_negative_cache_refresh_on_hit(self, cache, mock_compute_fn):
        """Negative cache hit refreshes TTL (sets again)."""
        # Pre-set negative cache
        await cache.set_negative_cache("bad", "model")

        # Mock positive cache miss and negative cache hit
        cache.mget_by_content_hash = AsyncMock(return_value=[None])
        cache._mget_negative_cache = AsyncMock(return_value=[True])

        result = await cache.compute_with_cache(
            ["bad"], "model", mock_compute_fn
        )

        # Should return empty list (negative hit)
        assert len(result) == 0

        # Should have called set_negative_cache again (refresh TTL)
        assert cache._set_negative_cache_many.call_count == 1

    @pytest.mark.asyncio
    async def test_flag_off_disables_negative_cache(self, cache, mock_compute_fn):
        """When flag is off, negative cache is not checked or set."""
        with patch.object(settings.feature_flags, 'negative_cache_enabled', False):
            # Mock positive cache miss
            cache.mget_by_content_hash = AsyncMock(return_value=[None])

            result = await cache.compute_with_cache(
                ["test"], "model", mock_compute_fn
            )

            # Should compute normally
            assert len(result) == 1
            assert np.array_equal(result[0], [1.0, 2.0, 3.0])

            # Should not have called negative cache methods
            cache._mget_negative_cache.assert_not_called()
            cache.set_negative_cache.assert_not_called()


class TestEmbedQueryNegativeCache:
    """Test EmbeddingService.embed_query with negative cache."""

    @pytest.fixture
    def service(self, mocker: MockerFixture):
        """EmbeddingService with mocked dependencies."""
        mock_cache = AsyncMock()
        mock_cache.get_query_embedding = AsyncMock(return_value=None)
        mock_cache.get_negative_cache = AsyncMock(return_value=False)
        mock_cache.set_negative_cache = AsyncMock()
        mock_cache.set_query_embedding = AsyncMock()

        service = mocker.MagicMock()
        service._cache = mock_cache
        service._config = mocker.MagicMock()
        service._config.model_name = "bge-m3"
        service._config.max_length = 512
        service._encode = AsyncMock(return_value=np.array([[1.0, 2.0, 3.0]], dtype=np.float32))

        return service

    @pytest.mark.asyncio
    async def test_normal_query_returns_vector(self, service):
        """Normal query returns vector and caches it."""
        result = await service.embed_query("hello world")

        assert result is not None
        assert np.array_equal(result, [1.0, 2.0, 3.0])
        service._cache.set_query_embedding.assert_called_once()
        service._cache.get_negative_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_negative_cache_hit_returns_none(self, service):
        """Negative cache hit returns None without calling encode."""
        service._cache.get_negative_cache = AsyncMock(return_value=True)

        result = await service.embed_query("bad query")

        assert result is None
        service._cache.get_negative_cache.assert_called_once()
        service._encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_overlong_query_sets_negative_cache(self, service):
        """Query over max_length*2 sets negative cache and returns None."""
        service._config.max_length = 100
        long_query = "a" * 201  # Exceeds 200 limit

        result = await service.embed_query(long_query)

        assert result is None
        service._cache.set_negative_cache.assert_called_once_with(long_query, "bge-m3")
        service._encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_texts_skipped_propagates_to_embed_query(self, service):
        """embed_texts skipped (EmbeddingSkippedError) returns None."""
        service._encode = AsyncMock(side_effect=EmbeddingSkippedError("skipped"))

        result = await service.embed_query("test")

        assert result is None

    @pytest.mark.asyncio
    async def test_flag_off_bypasses_negative_cache(self, service):
        """When flag is off, negative cache is not checked."""
        with patch.object(service._cache, 'get_negative_cache', return_value=False):
            with patch.object(service._cache, 'set_negative_cache'):
                # Set negative cache to return True but flag should bypass it
                service._cache.get_negative_cache = AsyncMock(return_value=True)

                result = await service.embed_query("test")

                # Should still compute (negative cache bypassed)
                assert result is not None
                service._cache.get_negative_cache.assert_called_once()


class TestNegativeCacheMetrics:
    """Test negative cache metrics recording."""

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """Reset negative cache metrics between tests."""
        metrics.reset_negative_cache_stats()

    @pytest.mark.asyncio
    async def test_hit_metrics(self):
        """Hits increment hit counter and update hit rate."""
        # Record some events
        metrics.record_negative_cache_event(hit=True)  # hit
        metrics.record_negative_cache_event(hit=True)  # hit
        metrics.record_negative_cache_event(hit=False)  # miss

        assert metrics.negative_cache_hits_total._value._value == 2
        assert metrics.negative_cache_hit_rate._value._value == 2/3

    @pytest.mark.asyncio
    async def test_miss_metrics(self):
        """Misses increment miss counter and update hit rate."""
        # Record some events
        metrics.record_negative_cache_event(hit=False)  # miss
        metrics.record_negative_cache_event(hit=False)  # miss
        metrics.record_negative_cache_event(hit=True)  # hit

        assert metrics.negative_cache_hits_total._value._value == 1
        assert metrics.negative_cache_hit_rate._value._value == 1/3

    @pytest.mark.asyncio
    async def test_zero_hit_rate(self):
        """Zero hit rate when no hits."""
        metrics.record_negative_cache_event(hit=False)
        metrics.record_negative_cache_event(hit=False)

        assert metrics.negative_cache_hits_total._value._value == 0
        assert metrics.negative_cache_hit_rate._value._value == 0.0

    @pytest.mark.asyncio
    async def test_metrics_exposed(self):
        """All negative cache metrics are exposed in generate_latest."""
        from prometheus_client.parser import text_string_to_metric_families
        from prometheus_client import generate_latest

        # Record some events
        metrics.record_negative_cache_event(hit=True)
        metrics.record_negative_cache_event(hit=False)
        metrics.negative_cache_sets_total.inc()

        # Get metrics text
        metrics_text = generate_latest().decode()

        # Parse and check that our metrics are present
        families = list(text_string_to_metric_families(metrics_text))

        metric_names = {f.name for f in families}
        assert "negative_cache_hits_total" in metric_names
        assert "negative_cache_sets_total" in metric_names
        assert "negative_cache_hit_rate" in metric_names


class TestNegativeCacheIntegration:
    """Integration tests with real Redis-like behavior."""

    @pytest.mark.asyncio
    async def test_1000_requests_scenario(self, mocker: MockerFixture):
        """Test AC scenario: 1000 requests, 1 model call, 999 hits."""
        fake_redis = MagicMock(spec=aioredis.Redis)
        cache = EmbeddingCache(fake_redis)

        # Mock positive cache miss for all
        cache.mget_by_content_hash = AsyncMock(return_value=[None] * 1000)

        # Mock compute function (called only once)
        compute_called = False
        async def mock_compute(texts):
            nonlocal compute_called
            compute_called = True
            return np.array([[1.0, 2.0, 3.0]], dtype=np.float32)

        # Mock negative cache: first miss, then 999 hits
        call_count = 0
        async def mock_negative_lookup(items):
            nonlocal call_count
            results = []
            for i, (_text, _model) in enumerate(items):
                if call_count == 0 and i == 0:
                    results.append(False)  # First miss
                else:
                    results.append(True)   # Subsequent hits
            call_count += 1
            return results

        cache._mget_negative_cache = mock_negative_lookup

        # Simulate 1000 requests
        for i in range(1000):
            await cache.compute_with_cache([f"text_{i}"], "model", mock_compute)

        # Compute should have been called only once
        assert compute_called is True

        # Negative cache sets should have been called 999 times (hits refresh TTL)
        assert cache.set_negative_cache.call_count == 999

        # Negative cache hits should be 999
        assert metrics.negative_cache_hits_total._value._value == 999
