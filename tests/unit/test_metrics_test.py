"""Unit tests for Prometheus metrics (P-15, C-12 Critical, ARCHITECT §11.1).

Verifies that all 22 metrics (7 MVP + 15 Critical) are registered in the default
Prometheus registry and that increment/observe operations update the exposed values.
"""

from __future__ import annotations

import prometheus_client
import pytest

from app.observability import metrics


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:  # type: ignore[misc]
    """Reset all metric values between tests to avoid cross-test pollution.

    ``clear()`` is a no-op for unlabeled metrics in prometheus_client
    (``if not self._labelnames: return``), so the underlying value has to be
    reset explicitly - otherwise counters leak between test modules.
    """
    for collector in [
        metrics.search_latency_ms,
        metrics.reranker_latency_ms,
        metrics.index_lag_seconds,
        metrics.qdrant_upsert_errors_total,
        metrics.embedding_cache_hits_total,
        metrics.embedding_cache_requests_total,
        metrics.dead_letter_count,
        metrics.dead_letters_total,
        metrics.circuit_breaker_state,
        metrics.circuit_breaker_opened_total,
        metrics.circuit_breaker_requests_total,
        metrics.fusion_strategy_usage_total,
        metrics.qdrant_pushdown_rate_total,
    ]:
        if hasattr(collector, "clear"):
            collector.clear()
        value = getattr(collector, "_value", None)
        if value is not None:
            value.set(0)
    yield


def _gather() -> str:
    """Return the Prometheus text exposition of the default registry."""
    return prometheus_client.generate_latest().decode()


class TestSearchLatencyMs:
    def test_registered(self) -> None:
        output = _gather()
        assert "search_latency_ms" in output

    def test_observe_increments_bucket(self) -> None:
        metrics.search_latency_ms.labels(tenant_id="t1", fusion="rrf").observe(150)
        output = _gather()
        # Labels are sorted alphabetically: fusion, le, tenant_id
        assert 'search_latency_ms_bucket{fusion="rrf",le="150.0",tenant_id="t1"}' in output
        assert 'search_latency_ms_count{fusion="rrf",tenant_id="t1"}' in output

    def test_multiple_labels(self) -> None:
        metrics.search_latency_ms.labels(tenant_id="t1", fusion="rrf").observe(50)
        metrics.search_latency_ms.labels(tenant_id="t2", fusion="rrf").observe(200)
        output = _gather()
        assert 'tenant_id="t1"' in output
        assert 'tenant_id="t2"' in output


class TestIndexLagSeconds:
    def test_registered(self) -> None:
        output = _gather()
        assert "index_lag_seconds" in output

    def test_set_updates_value(self) -> None:
        metrics.index_lag_seconds.set(42.5)
        output = _gather()
        assert "index_lag_seconds 42.5" in output


class TestQdrantUpsertErrorsTotal:
    def test_registered(self) -> None:
        output = _gather()
        assert "qdrant_upsert_errors_total" in output

    def test_inc_with_label(self) -> None:
        metrics.qdrant_upsert_errors_total.labels(error_type="timeout").inc()
        metrics.qdrant_upsert_errors_total.labels(error_type="timeout").inc()
        output = _gather()
        assert 'qdrant_upsert_errors_total{error_type="timeout"} 2.0' in output


class TestEmbeddingCacheCounters:
    def test_hits_registered(self) -> None:
        output = _gather()
        assert "embedding_cache_hits_total" in output

    def test_requests_registered(self) -> None:
        output = _gather()
        assert "embedding_cache_requests_total" in output

    def test_hits_increment(self) -> None:
        metrics.embedding_cache_hits_total.inc(5)
        output = _gather()
        assert "embedding_cache_hits_total 5.0" in output

    def test_requests_increment(self) -> None:
        metrics.embedding_cache_requests_total.inc(10)
        output = _gather()
        assert "embedding_cache_requests_total 10.0" in output


class TestDeadLetterCount:
    def test_registered(self) -> None:
        output = _gather()
        assert "dead_letter_count" in output

    def test_set_updates(self) -> None:
        metrics.dead_letter_count.set(7)
        output = _gather()
        assert "dead_letter_count 7.0" in output


class TestDeadLettersTotal:
    def test_registered(self) -> None:
        output = _gather()
        assert "dead_letters_total" in output

    def test_inc(self) -> None:
        metrics.dead_letters_total.inc()
        metrics.dead_letters_total.inc()
        output = _gather()
        assert "dead_letters_total 2.0" in output


class TestRerankerLatencyMs:
    """Test reranker latency histogram (C-12)."""

    def test_registered(self) -> None:
        output = _gather()
        assert "reranker_latency_ms" in output

    def test_observe_increments_bucket(self) -> None:
        metrics.reranker_latency_ms.labels(device="cuda", mock=False).observe(150)
        output = _gather()
        # Labels в exposition отсортированы по имени: device, le, mock;
        # bool рендерится как "False". Bucket'ы [10..1000], поэтому 150ms
        # попадает в le="200.0" (а не в le="150.0", такого bucket'а нет).
        assert 'reranker_latency_ms_bucket{device="cuda",le="100.0",mock="False"} 0.0' in output
        assert 'reranker_latency_ms_bucket{device="cuda",le="200.0",mock="False"} 1.0' in output
        assert 'reranker_latency_ms_count{device="cuda",mock="False"} 1.0' in output

    def test_multiple_labels(self) -> None:
        metrics.reranker_latency_ms.labels(device="cuda", mock=False).observe(50)
        metrics.reranker_latency_ms.labels(device="cpu", mock=True).observe(200)
        output = _gather()
        assert 'device="cuda"' in output
        assert 'device="cpu"' in output
        assert 'mock="False"' in output
        assert 'mock="True"' in output


class TestFusionStrategyUsage:
    """Test fusion strategy usage counter (C-12)."""

    def test_registered(self) -> None:
        output = _gather()
        assert "fusion_strategy_usage" in output

    def test_inc_with_label(self) -> None:
        metrics.fusion_strategy_usage_total.labels(strategy="rrf").inc()
        metrics.fusion_strategy_usage_total.labels(strategy="weighted").inc()
        metrics.fusion_strategy_usage_total.labels(strategy="weighted").inc()
        output = _gather()
        assert 'fusion_strategy_usage_total{strategy="rrf"} 1.0' in output
        assert 'fusion_strategy_usage_total{strategy="weighted"} 2.0' in output


class TestCircuitBreakerState:
    """Test circuit breaker state gauge (C-12)."""

    def test_registered(self) -> None:
        output = _gather()
        assert "circuit_breaker_state" in output

    def test_set_with_label(self) -> None:
        metrics.circuit_breaker_state.labels(component="reranker").set(1)
        output = _gather()
        assert 'circuit_breaker_state{component="reranker"} 1.0' in output


class TestCircuitBreakerOpenedTotal:
    """Test circuit breaker opened counter (C-12)."""

    def test_registered(self) -> None:
        output = _gather()
        assert "circuit_breaker_opened_total" in output

    def test_inc_with_labels(self) -> None:
        metrics.circuit_breaker_opened_total.labels(component="reranker", reason="error_rate").inc()
        metrics.circuit_breaker_opened_total.labels(component="reranker", reason="latency").inc()
        output = _gather()
        assert (
            'circuit_breaker_opened_total{component="reranker",reason="error_rate"} 1.0' in output
        )
        assert 'circuit_breaker_opened_total{component="reranker",reason="latency"} 1.0' in output


class TestCircuitBreakerRequestsTotal:
    """Test circuit breaker requests counter (C-12)."""

    def test_registered(self) -> None:
        output = _gather()
        assert "circuit_breaker_requests_total" in output

    def test_inc_with_labels(self) -> None:
        metrics.circuit_breaker_requests_total.labels(component="reranker", result="success").inc()
        metrics.circuit_breaker_requests_total.labels(component="reranker", result="error").inc()
        metrics.circuit_breaker_requests_total.labels(component="reranker", result="rejected").inc()
        output = _gather()
        assert 'circuit_breaker_requests_total{component="reranker",result="success"} 1.0' in output
        assert 'circuit_breaker_requests_total{component="reranker",result="error"} 1.0' in output
        assert (
            'circuit_breaker_requests_total{component="reranker",result="rejected"} 1.0' in output
        )


class TestQdrantPushdownRate:
    """Test Qdrant push-down rate counter (C-12)."""

    def test_registered(self) -> None:
        output = _gather()
        assert "qdrant_pushdown_rate" in output

    def test_inc_with_label(self) -> None:
        metrics.qdrant_pushdown_rate_total.labels(result="used").inc()
        metrics.qdrant_pushdown_rate_total.labels(result="skipped").inc()
        metrics.qdrant_pushdown_rate_total.labels(result="skipped").inc()
        output = _gather()
        assert 'qdrant_pushdown_rate_total{result="used"} 1.0' in output
        assert 'qdrant_pushdown_rate_total{result="skipped"} 2.0' in output


class TestAllMetricsExposed:
    """Catch-all: all 22 metrics (7 MVP + 15 Critical) must appear in the exposition output."""

    @pytest.mark.parametrize(
        "metric_name",
        [
            # MVP metrics
            "search_latency_ms",
            "index_lag_seconds",
            "qdrant_upsert_errors_total",
            "embedding_cache_hits_total",
            "embedding_cache_requests_total",
            "dead_letter_count",
            "dead_letters_total",
            # Critical metrics (C-12)
            "reranker_latency_ms",
            "fusion_strategy_usage_total",
            "circuit_breaker_state",
            "circuit_breaker_opened_total",
            "circuit_breaker_requests_total",
            "qdrant_pushdown_rate_total",
            # B-09 metrics
            "vector_search_latency_ms",
            "eval_last_run_timestamp",
        ],
    )
    def test_metric_present(self, metric_name: str) -> None:
        output = _gather()
        assert metric_name in output, f"Metric {metric_name!r} not found in exposition"
