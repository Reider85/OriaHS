"""Unit tests for Prometheus metrics (P-15, ARCHITECT §11.1).

Verifies that every MVP metric is registered in the default Prometheus
registry and that increment/observe operations update the exposed values.
"""

from __future__ import annotations

import prometheus_client
import pytest

from app.observability import metrics


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:  # type: ignore[misc]
    """Reset all metric values between tests to avoid cross-test pollution."""
    for collector in [
        metrics.search_latency_ms,
        metrics.index_lag_seconds,
        metrics.qdrant_upsert_errors_total,
        metrics.embedding_cache_hits_total,
        metrics.embedding_cache_requests_total,
        metrics.dead_letter_count,
        metrics.dead_letters_total,
    ]:
        if hasattr(collector, "clear"):
            collector.clear()
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


class TestAllMetricsExposed:
    """Catch-all: every MVP metric must appear in the exposition output."""

    @pytest.mark.parametrize(
        "metric_name",
        [
            "search_latency_ms",
            "index_lag_seconds",
            "qdrant_upsert_errors_total",
            "embedding_cache_hits_total",
            "embedding_cache_requests_total",
            "dead_letter_count",
            "dead_letters_total",
        ],
    )
    def test_metric_present(self, metric_name: str) -> None:
        output = _gather()
        assert metric_name in output, f"Metric {metric_name!r} not found in exposition"
