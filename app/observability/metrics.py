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
# Reranker latency — filled by RerankerService after every rerank (C-12)
# Buckets in milliseconds (C-12: p99 ≤ 350 ms for 50 pairs on GPU)
# ---------------------------------------------------------------------------
reranker_latency_ms = Histogram(
    "reranker_latency_ms",
    "Cross-encoder reranking latency in milliseconds (per query).",
    labelnames=["device", "mock"],
    buckets=[10, 25, 50, 100, 200, 300, 500, 1000],
)

# ---------------------------------------------------------------------------
# Fusion strategy usage — filled by SearchOrchestrator after every search (C-12)
# Имя с _total: prometheus_client сам дописывает _total к экспонируемому имени
# Counter, поэтому без суффикса Python-имя и метрика в Grafana/Prometheus
# расходились бы (а любой promql по fusion_strategy_usage не нашёл бы серию).
# ---------------------------------------------------------------------------
fusion_strategy_usage_total = Counter(
    "fusion_strategy_usage_total",
    "Total search requests by fusion strategy.",
    labelnames=["strategy"],  # "rrf", "weighted", "rerank", "weighted+rerank"
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
    labelnames=["component"],  # "reranker"
)

circuit_breaker_opened_total = Counter(
    "circuit_breaker_opened_total",
    "Total circuit breaker open transitions (closed → open).",
    labelnames=["component", "reason"],  # "reranker", "error_rate", "latency"
)

circuit_breaker_requests_total = Counter(
    "circuit_breaker_requests_total",
    "Circuit breaker call results",
    labelnames=["component", "result"],  # "reranker", "success", "error", "rejected"
)

# ---------------------------------------------------------------------------
# Evaluation metrics — updated by NightlyEvalJob (C-07)
# ---------------------------------------------------------------------------
eval_recall_at_10 = Gauge(
    "eval_recall_at_10",
    "Recall@10 score for evaluation strategies.",
    labelnames=["strategy", "dataset_version"],
)

eval_ndcg_at_10 = Gauge(
    "eval_ndcg_at_10",
    "nDCG@10 score for evaluation strategies.",
    labelnames=["strategy", "dataset_version"],
)

eval_mrr = Gauge(
    "eval_mrr",
    "Mean Reciprocal Rank for evaluation strategies.",
    labelnames=["strategy", "dataset_version"],
)

eval_runs_total = Counter(
    "eval_runs_total",
    "Total evaluation runs completed.",
    labelnames=["strategy"],
)

eval_regression_detected_total = Counter(
    "eval_regression_detected_total",
    "Total evaluation runs that detected regressions.",
)

# ---------------------------------------------------------------------------
# Degraded mode metrics — updated by SearchOrchestrator (C-09)
# ---------------------------------------------------------------------------
search_degraded_total = Counter(
    "search_degraded_total",
    "Total degraded search requests.",
    labelnames=["reason"],  # "qdrant_unavailable", "deadline_exceeded", "vector_disabled"
)

search_partial_total = Counter(
    "search_partial_total",
    "Total search requests that returned partial results.",
)

# ---------------------------------------------------------------------------
# Push-down filter metrics — updated by SearchOrchestrator (C-10)
# ---------------------------------------------------------------------------
qdrant_pushdown_rate_total = Counter(
    "qdrant_pushdown_rate_total",
    "Push-down filter usage decisions.",
    labelnames=[
        "result"
    ],  # "used", "skipped", "too_large", "no_filters", "disabled", "not_selective"
)

# Legacy alias for backward compatibility
pushdown_total = qdrant_pushdown_rate_total

pushdown_selectivity = Gauge(
    "pushdown_selectivity",
    "Last selectivity estimate for push-down filter decision.",
)

# ---------------------------------------------------------------------------
# Throttle metrics — updated by OutboxThrottle (C-11)
# ---------------------------------------------------------------------------
outbox_throttled_total = Counter(
    "outbox_throttled_total",
    "Total index requests throttled (202 Accepted).",
)

outbox_reindex_triggered_total = Counter(
    "outbox_reindex_triggered_total",
    "Total full reindex triggers from outbox overflow.",
)

# ---------------------------------------------------------------------------
# Vector search latency — filled by vector_search after every vector channel call (B-09)
# Buckets in milliseconds (C-10: push-down p99 ≤ 50ms)
# ---------------------------------------------------------------------------
vector_search_latency_ms = Histogram(
    "vector_search_latency_ms",
    "Vector channel (Qdrant kNN) latency in milliseconds.",
    labelnames=["pushdown"],  # "true", "false"
    buckets=[5, 10, 25, 50, 100, 200, 500, 1000],
)

# ---------------------------------------------------------------------------
# Nightly eval timestamp — filled by NightlyEvalJob after each run (B-09)
# ---------------------------------------------------------------------------
eval_last_run_timestamp = Gauge(
    "eval_last_run_timestamp",
    "Unix timestamp of the last completed nightly eval run.",
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
    "reranker_latency_ms",
    "fusion_strategy_usage_total",
    "circuit_breaker_state",
    "circuit_breaker_opened_total",
    "circuit_breaker_requests_total",
    "eval_recall_at_10",
    "eval_ndcg_at_10",
    "eval_mrr",
    "eval_runs_total",
    "eval_regression_detected_total",
    "search_degraded_total",
    "search_partial_total",
    "qdrant_pushdown_rate_total",
    "pushdown_selectivity",
    "outbox_throttled_total",
    "outbox_reindex_triggered_total",
    "vector_search_latency_ms",
    "eval_last_run_timestamp",
]
