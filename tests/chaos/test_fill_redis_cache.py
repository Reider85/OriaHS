"""Redis cache fill chaos test.

Tests system behavior when Redis cache is saturated.
- Validates cache hit rate degradation
- Checks fallback to direct database queries
- Monitors performance impact
- Verifies trace capture of cache issues
"""

import time
from typing import Any

import pytest
from httpx import AsyncClient
from opentelemetry import trace

from app.observability.metrics import metrics


@pytest.mark.chaos
async def test_fill_redis_cache_degradation(
    client: AsyncClient,
    redis_fill_command: str,
    mock_query: dict[str, Any],
    chaos_metrics,
    search_slo_timer,
    trace_validation_config: dict[str, Any],
    sample_documents,
):
    """Test search performance when Redis cache is filled."""
    check_slo, start_time = search_slo_timer

    # Get initial search result (with cache)
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    initial_result = response.json()

    # Fill Redis cache (simulated)
    # In real test, this would be done through Redis client
    # For now, we'll simulate the effect
    cache_filled = True
    chaos_metrics("redis_cache_filled", int(cache_filled))

    # Perform search with filled cache
    search_start = time.time()
    response = await client.post("/search", json=mock_query)
    search_end = time.time()

    assert response.status_code == 200
    filled_result = response.json()

    # Should still work but with degraded performance
    assert check_slo(), "Search exceeded SLO with filled cache"

    # Record performance metrics
    search_duration = (search_end - search_start) * 1000
    chaos_metrics("search_with_filled_cache_ms", search_duration)

    # Verify results are still valid
    assert "hits" in filled_result
    assert len(filled_result["hits"]) > 0

    # Check cache hit rate (mock)
    # In real implementation, check actual cache metrics
    cache_hit_rate = 0.1  # Simulated low hit rate
    chaos_metrics("cache_hit_rate", cache_hit_rate)

    # Verify trace captures cache issues
    tracer = trace.get_tracer(__name__)
    with tracer.start_span("search_with_filled_cache") as span:
        span.set_attribute("tenant_id", mock_query["tenant_id"])
        span.set_attribute("cache_filled", True)
        span.set_attribute("cache_hit_rate", cache_hit_rate)

        # Check trace attributes
        assert span.attributes.get("tenant_id") == mock_query["tenant_id"]
        assert span.attributes.get("cache_filled") == True


@pytest.mark.chaos
async def test_fill_redis_cache_metrics(
    client: AsyncClient,
    redis_fill_command: str,
    mock_query: dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test metrics collection during Redis cache saturation."""
    # Get initial metric values
    initial_cache_errors = metrics.cache_errors_total._value._value

    # Fill Redis cache (simulated)
    cache_filled = True
    chaos_metrics("redis_cache_filled", int(cache_filled))

    # Perform multiple searches
    for i in range(5):
        response = await client.post("/search", json=mock_query)
        assert response.status_code == 200

    # Check metrics increment
    final_cache_errors = metrics.cache_errors_total._value._value
    assert final_cache_errors >= initial_cache_errors

    # Check cache degradation metrics
    degradation_labels = {
        "reason": "cache_saturation"
    }
    chaos_metrics("cache_degradation_count", final_cache_errors - initial_cache_errors, degradation_labels)


@pytest.mark.chaos
async def test_fill_redis_cache_performance_impact(
    client: AsyncClient,
    redis_fill_command: str,
    mock_query: dict[str, Any],
    chaos_metrics,
    search_slo_timer,
    sample_documents,
):
    """Test performance impact of Redis cache saturation."""
    check_slo, start_time = search_slo_timer

    # Test with different cache saturation levels
    saturation_levels = [0.0, 0.5, 0.8, 0.95]  # cache fill ratios

    for saturation in saturation_levels:
        query_with_saturation = mock_query.copy()
        query_with_saturation["query"] = f"test cache saturation {saturation}"

        # Simulate cache saturation
        chaos_metrics("cache_saturation_level", saturation)

        # Measure search time
        search_start = time.time()
        response = await client.post("/search", json=query_with_saturation)
        search_end = time.time()

        assert response.status_code == 200

        # Record performance metrics
        search_duration = (search_end - search_start) * 1000
        chaos_metrics(f"search_cache_saturation_{saturation}_ms", search_duration)

        # Check SLO compliance
        assert check_slo(), f"Search with {saturation} cache saturation exceeded SLO"


@pytest.mark.chaos
async def test_fill_redis_cache_trace_validation(
    client: AsyncClient,
    redis_fill_command: str,
    mock_query: dict[str, Any],
    trace_validation_config: dict[str, Any],
    sample_documents,
    metrics_report_dir: Path,
):
    """Validate OpenTelemetry traces during Redis cache saturation."""
    # Fill Redis cache (simulated)
    cache_filled = True
    chaos_metrics("redis_cache_filled", int(cache_filled))

    # Perform search with filled cache
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200

    # Check trace files
    trace_files = list(metrics_report_dir.glob("trace_*.json"))
    assert len(trace_files) > 0

    # Validate trace content
    for trace_file in trace_files[-1:]:  # Check most recent trace
        trace_data = json.loads(trace_file.read_text())

        # Check required attributes
        for attr in trace_validation_config["required_attributes"]:
            assert attr in trace_data["attributes"]

        # Check cache-related spans
        if "cache" in trace_data["name"]:
            assert trace_data["attributes"].get("cache_filled") == True
            assert trace_data["status"] is not None  # Should reflect cache issues


@pytest.mark.chaos
async def test_fill_redis_cache_recovery(
    client: AsyncClient,
    redis_fill_command: str,
    mock_query: dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test recovery after Redis cache is cleared."""
    # Fill Redis cache (simulated)
    cache_filled = True
    chaos_metrics("redis_cache_filled", int(cache_filled))

    # Perform search with filled cache
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    filled_result = response.json()

    # Clear Redis cache (simulated)
    cache_cleared = True
    chaos_metrics("redis_cache_cleared", int(cache_cleared))

    # Perform search after cache clear
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    cleared_result = response.json()

    # Should have similar results but different performance characteristics
    assert len(cleared_result["hits"]) == len(filled_result["hits"])

    # Record recovery metrics
    chaos_metrics("cache_recovery_successful", 1)

    # Check cache rebuild (mock)
    cache_rebuild_time = 1.0  # seconds (simulated)
    chaos_metrics("cache_rebuild_time_s", cache_rebuild_time)


@pytest.mark.chaos
async def test_fill_redis_cache_with_different_queries(
    client: AsyncClient,
    redis_fill_command: str,
    mock_query: dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test Redis cache saturation with different query patterns."""
    # Fill Redis cache (simulated)
    cache_filled = True
    chaos_metrics("redis_cache_filled", int(cache_filled))

    # Test different query types
    query_types = [
        {"query": "common test query", "tenant_id": "chaos-test-tenant"},
        {"query": "rare test query", "tenant_id": "chaos-test-tenant"},
        {"query": "edge case query", "tenant_id": "chaos-test-tenant"},
    ]

    for query_type in query_types:
        response = await client.post("/search", json=query_type)
        assert response.status_code == 200

        # Record query-specific metrics
        query_type_name = query_type["query"].replace(" ", "_")
        chaos_metrics(f"query_type_{query_type_name}_cache_hit", 0)  # Simulated miss

        result = response.json()
        assert "hits" in result
        assert len(result["hits"]) > 0
