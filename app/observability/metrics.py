"""Prometheus metric registry (P-15 full set, ARCHITECT §11.1).

All application-level Prometheus collectors are defined here as module-level
singletons so they register with the default registry exactly once per
process (import caching).  ``prometheus-fastapi-instrumentator`` handles the
standard ``http_requests_total`` / ``http_request_duration_seconds`` metrics
automatically — those are NOT declared here.

Naming convention: ``_total`` suffix for counters, ``_bucket``/``_sum``/``_count``
for histograms.  Grafana dashboards (P-16) reference these exact names.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Search latency — filled by SearchOrchestrator after every /search (P-11)
# Buckets in milliseconds (ARCHITECT §11.1: p99 ≤ 120 ms target)
# ---------------------------------------------------------------------------
search_latency_ms = Histogram(
    "search_latency_ms",
    "End-to-end search latency in milliseconds (lexical + vector + fusion).",
    labelnames=["tenant_id", "fusion"],
    buckets=[10, 25, 50, 100, 150, 200, 300, 500, 1000, 2000],
)

# ---------------------------------------------------------------------------
# Indexing lag — filled by ReconcilerWorker every poll cycle
# ---------------------------------------------------------------------------
index_lag_seconds = Gauge(
    "index_lag_seconds",
    "Seconds since the oldest pending/failed search_outbox row was created.",
)

# ---------------------------------------------------------------------------
# Qdrant upsert errors — incremented in ReconcilerWorker on upsert failure
# ---------------------------------------------------------------------------
qdrant_upsert_errors_total = Counter(
    "qdrant_upsert_errors_total",
    "Total Qdrant upsert/delete failures in the reconciler.",
    labelnames=["error_type"],
)

# ---------------------------------------------------------------------------
# Embedding cache hit/miss counters — incremented in EmbeddingCache (P-06)
# Rate is computed in Grafana: rate(hits[5m]) / rate(requests[5m])
# ---------------------------------------------------------------------------
embedding_cache_hits_total = Counter(
    "embedding_cache_hits_total",
    "Embedding cache lookups that returned a cached vector.",
)

embedding_cache_requests_total = Counter(
    "embedding_cache_requests_total",
    "Total embedding cache lookup attempts (hits + misses).",
)

# ---------------------------------------------------------------------------
# Dead-letter gauge — periodically refreshed by ReconcilerWorker
# ---------------------------------------------------------------------------
dead_letter_count = Gauge(
    "dead_letter_count",
    "Current number of search_outbox rows in the 'dead' state.",
)

# ---------------------------------------------------------------------------
# Dead-letter counter — incremented when a row transitions to 'dead' (P-13)
# ---------------------------------------------------------------------------
dead_letters_total = Counter(
    "dead_letters_total",
    "Total search_outbox rows moved to the 'dead' state after max_attempts.",
)

# ---------------------------------------------------------------------------
# Outbox pending count — updated by ReconcilerWorker (P-16)
# ---------------------------------------------------------------------------
outbox_pending_count = Gauge(
    "outbox_pending_count",
    "Current number of search_outbox rows in 'pending' or 'failed' status.",
)

# ---------------------------------------------------------------------------
# Reconciler batch size — updated by ReconcilerWorker (P-16)
# ---------------------------------------------------------------------------
reconciler_batch_size = Gauge(
    "reconciler_batch_size",
    "Batch size used by ReconcilerWorker (configurable, default 500).",
)

# ---------------------------------------------------------------------------
# Circuit breaker metrics — updated by RerankerCircuitBreaker (C-03)
# ---------------------------------------------------------------------------
circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=half_open, 2=open).",
)

circuit_breaker_opened_total = Counter(
    "circuit_breaker_opened_total",
    "Total circuit breaker open transitions (closed → open).",
)

circuit_breaker_requests_total = Counter(
    "circuit_breaker_requests_total",
    "Circuit breaker call results",
    labelnames=["result"],  # "success", "error", "rejected"
)

__all__ = [
    "dead_letter_count",
    "dead_letters_total",
    "embedding_cache_hits_total",
    "embedding_cache_requests_total",
    "index_lag_seconds",
    "outbox_pending_count",
    "qdrant_upsert_errors_total",
    "reconciler_batch_size",
    "search_latency_ms",
    "circuit_breaker_state",
    "circuit_breaker_opened_total",
    "circuit_breaker_requests_total",
]
