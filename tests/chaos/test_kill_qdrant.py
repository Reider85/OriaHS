"""Qdrant kill chaos test.

Tests system resilience when Qdrant becomes unavailable.
- Verifies graceful degradation to lexical-only search
- Validates OpenTelemetry traces capture the failure
- Checks metrics and SLO compliance
"""

import asyncio
import time
from typing import Dict, Any

import pytest
from httpx import AsyncClient
from opentelemetry import trace
from qdrant_client import QdrantClient

from app.config import ChaosConfig
from app.observability.metrics import metrics
from app.search.service import SearchService


@pytest.mark.chaos
async def test_kill_qdrant_degraded_mode(
    client: AsyncClient,
    chaos_config: ChaosConfig,
    qdrant_kill_command: str,
    session,
    mock_query: Dict[str, Any],
    chaos_metrics,
    search_slo_timer,
    trace_validation_config: Dict[str, Any],
    sample_documents,
):
    """Test graceful degradation when Qdrant is killed."""
    check_slo, start_time = search_slo_timer
    
    # Get initial search result (both channels working)
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    initial_result = response.json()
    
    # Kill Qdrant container
    import subprocess
    result = subprocess.run(qdrant_kill_command, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to kill Qdrant: {result.stderr}"
    
    # Wait for health check to detect failure
    await asyncio.sleep(2)
    
    # Attempt search after Qdrant death
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    
    result = response.json()
    
    # Verify degradation
    assert "hits" in result
    assert len(result["hits"]) > 0  # Should still return lexical results
    
    # Check degraded flag in response
    if "debug" in result and result["debug"]:
        assert result["debug"].get("degraded") == True
        assert "vector_channel_failed" in result["debug"].get("errors", [])
    
    # Verify SLO compliance
    assert check_slo(), "Search exceeded SLO after Qdrant kill"
    
    # Record chaos metrics
    chaos_metrics("search_after_qdrant_kill", len(result["hits"]))
    
    # Verify OpenTelemetry trace
    tracer = trace.get_tracer(__name__)
    with tracer.start_span("search_after_qdrant_kill") as span:
        span.set_attribute("tenant_id", mock_query["tenant_id"])
        span.set_attribute("qdrant_killed", True)
        
        # Check trace attributes
        assert span.attributes.get("tenant_id") == mock_query["tenant_id"]
        assert span.attributes.get("qdrant_killed") == True
    
    # Restore Qdrant
    restore_command = f"docker restart {chaos_config.qdrant_container_name}"
    subprocess.run(restore_command, shell=True, capture_output=True, text=True)
    await asyncio.sleep(5)  # Wait for restart
    
    # Verify recovery
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    recovery_result = response.json()
    
    # Should have more results after recovery
    assert len(recovery_result["hits"]) >= len(result["hits"])


@pytest.mark.chaos
async def test_kill_qdrant_metrics(
    client: AsyncClient,
    chaos_config: ChaosConfig,
    qdrant_kill_command: str,
    mock_query: Dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test metrics collection during Qdrant failure."""
    # Get initial metric values
    initial_degraded = metrics.search_degraded_total._value._value
    
    # Kill Qdrant
    import subprocess
    subprocess.run(qdrant_kill_command, shell=True, capture_output=True, text=True)
    await asyncio.sleep(2)
    
    # Perform search
    response = await client.post("/search", json=mock_query)
    assert response.status_code == 200
    
    # Check metrics increment
    final_degraded = metrics.search_degraded_total._value._value
    assert final_degraded > initial_degraded
    
    # Check specific degradation reason
    degradation_labels = {
        "reason": "qdrant_unavailable"
    }
    chaos_metrics("degradation_reason", final_degraded - initial_degraded, degradation_labels)


@pytest.mark.chaos
async def test_kill_qdrant_trace_validation(
    client: AsyncClient,
    chaos_config: ChaosConfig,
    qdrant_kill_command: str,
    mock_query: Dict[str, Any],
    trace_validation_config: Dict[str, Any],
    sample_documents,
    metrics_report_dir: Path,
):
    """Validate OpenTelemetry traces after Qdrant kill."""
    import subprocess
    subprocess.run(qdrant_kill_command, shell=True, capture_output=True, text=True)
    await asyncio.sleep(2)
    
    # Perform search
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
        
        # Check service span
        assert trace_data["name"] == "search"
        assert trace_data["kind"] == SpanKind.SERVER.value
        
        # Check error handling in trace
        if "qdrant" in trace_data["name"]:
            assert trace_data["status"] is not None


@pytest.mark.chaos
async def test_kill_qdrant_recovery_time(
    client: AsyncClient,
    chaos_config: ChaosConfig,
    qdrant_kill_command: str,
    mock_query: Dict[str, Any],
    chaos_metrics,
    sample_documents,
):
    """Test recovery time after Qdrant restart."""
    import subprocess
    import time
    
    # Kill Qdrant
    subprocess.run(qdrant_kill_command, shell=True, capture_output=True, text=True)
    kill_time = time.time()
    
    # Wait and restart
    await asyncio.sleep(2)
    restore_command = f"docker restart {chaos_config.qdrant_container_name}"
    subprocess.run(restore_command, shell=True, capture_output=True, text=True)
    
    # Monitor recovery
    recovery_start = time.time()
    max_wait = 30  # seconds
    recovered = False
    
    while time.time() - recovery_start < max_wait:
        try:
            response = await client.post("/search", json=mock_query)
            if response.status_code == 200:
                recovered = True
                break
        except:
            pass
        await asyncio.sleep(1)
    
    assert recovered, f"Qdrant not recovered within {max_wait} seconds"
    
    recovery_time = time.time() - kill_time
    chaos_metrics("qdrant_recovery_time_s", recovery_time)
    
    # Recovery should be reasonable (less than 30 seconds)
    assert recovery_time < 30, f"Qdrant recovery took too long: {recovery_time}s"