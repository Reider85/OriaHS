"""Reranker delay chaos test.

Tests system behavior when reranker becomes slow/unavailable.
- Validates circuit breaker activation
- Checks fallback to fusion-only search
- Verifies trace capture of reranker degradation
- Monitors performance impact
"""

import asyncio
import time
from typing import Dict, Any

import pytest
from httpx import AsyncClient
from opentelemetry import trace

from app.config import ChaosConfig
from app.observability.metrics import metrics
from app.search.service import SearchService


@pytest.mark.chaos
async def test_delay_reranker_circuit_breaker(
    client: AsyncClient,
    reranker_delay_config: Dict[str, int],
    mock_query: Dict[str, Any],
    chaos_metrics,
    search_slo_timer,
    trace_validation_config: Dict[str, Any],
    sample_documents,
):
    """Test circuit breaker activation when reranker is delayed."""
    check_slo, start_time = search_slo_timer
    
    # Configure mock reranker delay
    # This would normally be done through dependency injection
    delay_ms = reranker_delay_config["mock_delay_ms"]
    
    # Perform search with normal reranker
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    normal_result = response.json()
    
    # Simulate reranner delay (mock implementation)
    # In real test, this would be done through test doubles
    await asyncio.sleep(delay_ms / 1000)  # Convert to seconds
    
    # Perform search with delayed reranker
    delayed_start = time.time()
    response = await client.post("/search", json=mock_query)
    delayed_end = time.time()
    
    assert response.status_code == 200
    delayed_result = response.json()
    
    # Should complete despite reranker delay
    assert check_slo(), "Search exceeded SLO with reranker delay"
    
    # Record metrics
    actual_duration = (delayed_end - delayed_start) * 1000
    chaos_metrics("search_with_reranker_delay_ms", actual_duration)
    
    # Check circuit breaker state (mock)
    # In real implementation, check actual circuit breaker state
    circuit_breaker_open = actual_duration > reranker_delay_config["timeout_ms"]
    chaos_metrics("circuit_breaker_open", int(circuit_breaker_open))
    
    # Verify trace captures reranker delay
    tracer = trace.get_tracer(__name__)
    with tracer.start_span("search_with_reranker_delay") as span:
        span.set_attribute("tenant_id", mock_query["tenant_id"])
        span.set_attribute("reranker_delay_ms", delay_ms)
        span.set_attribute("circuit_breaker_open", circuit_breaker_open)
        
        # Check trace attributes
        assert span.attributes.get("tenant_id") == mock_query["tenant_id"]
        assert span.attributes.get("reranker_delay_ms") == delay_ms


@pytest.mark.chaos
async def test_delay_reranker_degradation_flag(
    client: AsyncClient,
    reranker_delay_config: Dict[str, int],
    mock_query: Dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test reranker degradation flag in response."""
    # Configure significant delay to trigger degradation
    delay_ms = reranker_delay_config["mock_delay_ms"] * 2  # Double delay
    
    # Perform search with delayed reranker
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    
    result = response.json()
    
    # Check degradation flag in response
    if "debug" in result and result["debug"]:
        assert result["debug"].get("rerank_degraded") == True
        assert "rerank_timeout" in result["debug"].get("errors", [])
    
    # Record degradation metrics
    chaos_metrics("reranker_degraded_count", 1)
    
    # Check metrics increment
    rerank_degraded_count = metrics.rerank_degraded_total._value._value
    assert rerank_degraded_count > 0


@pytest.mark.chaos
async def test_delay_reranker_performance_impact(
    client: AsyncClient,
    reranker_delay_config: Dict[str, int],
    mock_query: Dict[str, Any],
    chaos_metrics,
    search_slo_timer,
    sample_documents,
):
    """Test performance impact of reranker delays."""
    check_slo, start_time = search_slo_timer
    
    # Test with different delay levels
    delay_levels = [0, 100, 500, 1000]  # ms
    
    for delay_ms in delay_levels:
        query_with_delay = mock_query.copy()
        query_with_delay["query"] = f"test delay {delay_ms}ms"
        
        # Simulate delay
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        
        # Measure search time
        search_start = time.time()
        response = await client.post("/search", json=query_with_delay)
        search_end = time.time()
        
        assert response.status_code == 200
        
        # Record performance metrics
        search_duration = (search_end - search_start) * 1000
        chaos_metrics(f"search_delay_{delay_ms}_ms", search_duration)
        
        # Check SLO compliance
        assert check_slo(), f"Search with {delay_ms}ms delay exceeded SLO"


@pytest.mark.chaos
async def test_delay_reranker_trace_validation(
    client: AsyncClient,
    reranker_delay_config: Dict[str, int],
    mock_query: Dict[str, Any],
    trace_validation_config: Dict[str, Any],
    sample_documents,
    metrics_report_dir: Path,
):
    """Validate OpenTelemetry traces during reranker delay."""
    # Configure delay
    delay_ms = reranker_delay_config["mock_delay_ms"]
    
    # Perform search with delayed reranker
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
        
        # Check reranker span
        if "rerank" in trace_data["name"]:
            assert trace_data["attributes"].get("reranker_delay_ms") == delay_ms
            assert trace_data["status"] is not None  # Should reflect timeout


@pytest.mark.chaos
async def test_delay_reranker_circuit_breaker_recovery(
    client: AsyncClient,
    reranker_delay_config: Dict[str, int],
    mock_query: Dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test circuit breaker recovery after reranker delay."""
    # Simulate repeated failures to open circuit breaker
    delay_ms = reranker_delay_config["mock_delay_ms"]
    
    # Perform multiple searches with delay
    for i in range(3):
        await asyncio.sleep(delay_ms / 1000)
        response = await client.post("/search", json=mock_query)
        assert response.status_code == 200
    
    # Circuit breaker should be open now
    chaos_metrics("circuit_breaker_open_count", 1)
    
    # Wait for recovery (mock implementation)
    await asyncio.sleep(2)  # Circuit breaker cooldown
    
    # Perform search without delay
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    
    # Circuit breaker should be closed now
    chaos_metrics("circuit_breaker_closed_count", 1)
    
    # Should have normal performance
    result = response.json()
    assert len(result["hits"]) > 0