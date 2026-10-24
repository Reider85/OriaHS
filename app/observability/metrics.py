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
# OpenTelemetry tracing metrics — incremented by CountingSpanExporter
# ---------------------------------------------------------------------------
otel_spans_exported_total = Counter(
    "otel_spans_exported_total",
    "Total spans successfully exported to OTel collector.",
    labelnames=["job"],
)

otel_export_errors_total = Counter(
    "otel_export_errors_total",
    "Total span export failures (retries or errors).",
    labelnames=["job", "error_type"],
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
    "Number of searches that returned partial results due to timeout",
    ["tenant_id"],
)

# Personalization metrics (PR-01)
profile_vector_updates_total = Counter(
    "profile_vector_updates_total",
    "Total number of profile vector updates (EMA aggregation)",
    ["tenant_id"],
)
personalized_searches_total = Counter(
    "personalized_searches_total",
    "Total number of personalized searches",
    ["tenant_id", "cold_start"],
)
profile_vector_dimension = Gauge(
    "profile_vector_dimension",
    "Dimension of user profile vectors (should be 1024)",
    ["tenant_id"],
)

# Contextual personalization metrics (PR-05)
contextual_profile_used_total = Counter(
    "contextual_profile_used_total",
    "Total number of contextual profiles used for personalization",
    ["tenant_id", "context_key"],
)
session_profiles_expired_total = Counter(
    "session_profiles_expired_total",
    "Total number of expired session profiles cleaned up",
    ["tenant_id"],
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

# ---------------------------------------------------------------------------
# Search events metrics — PR-02 partitioning and archiving
# ---------------------------------------------------------------------------
search_events_inserted_total = Counter(
    "search_events_inserted_total",
    "Total search events inserted.",
    labelnames=["fusion_strategy"],
)

search_events_clicks_total = Counter(
    "search_events_clicks_total",
    "Total click events recorded.",
)

search_events_partitions_archived_total = Counter(
    "search_events_partitions_archived_total",
    "Total partitions archived to S3 Parquet.",
)

search_events_partitions_dropped_total = Counter(
    "search_events_partitions_dropped_total",
    "Total partitions dropped after archival.",
)

search_events_rows_total = Gauge(
    "search_events_rows_total",
    "Total rows across all search_events partitions.",
)

# ---------------------------------------------------------------------------
# A/B testing metrics — filled by ABTestingService and SearchOrchestrator (PR-03)
# ---------------------------------------------------------------------------
ab_experiments_active = Gauge(
    "ab_experiments_active",
    "Number of currently active A/B experiments.",
)

ab_assignments_total = Counter(
    "ab_assignments_total",
    "Total user assignments to experiment variants.",
    labelnames=["experiment_id", "variant"],
)

ab_significance_checked_total = Counter(
    "ab_significance_checked_total",
    "Total statistical significance checks for experiments.",
    labelnames=["experiment_id", "variant", "result"],
)

# ---------------------------------------------------------------------------
# MMR diversification metrics — filled by SearchOrchestrator (PR-04)
# ---------------------------------------------------------------------------
mmr_diversify_total = Counter(
    "mmr_diversify_total",
    "Total MMR diversification operations.",
    labelnames=["adaptive"],  # "true", "false"
)

mmr_diversity_score = Gauge(
    "mmr_diversity_score",
    "Diversity score from last MMR operation (mean(1 - max_sim)).",
)

mmr_lambda_param = Gauge(
    "mmr_lambda_param",
    "Lambda parameter used in last MMR operation.",
)

# ---------------------------------------------------------------------------
# Adaptive K metrics — filled by SearchOrchestrator (PR-10, ARCHITECT 6.4)
# ---------------------------------------------------------------------------
adaptive_k_used_total = Counter(
    "adaptive_k_used_total",
    "Total searches where adaptive K was applied, by tenant size bucket.",
    labelnames=["bucket"],  # "small", "medium", "large"
)

adaptive_k_lexical = Gauge(
    "adaptive_k_lexical",
    "K_lexical used in the last adaptive-K search.",
)

adaptive_k_vector = Gauge(
    "adaptive_k_vector",
    "K_vector used in the last adaptive-K search.",
)

# ---------------------------------------------------------------------------
# Migration framework metrics — filled by MigrationService (PR-07)
# ---------------------------------------------------------------------------
migration_backfill_progress = Gauge(
    "migration_backfill_progress",
    "Backfill progress as a percentage (0-100).",
    labelnames=["migration_id"],
)

migration_backfill_speed = Gauge(
    "migration_backfill_speed",
    "Backfill speed in documents per hour.",
    labelnames=["migration_id"],
)

migration_canary_similarity = Gauge(
    "migration_canary_similarity",
    "Canary reindex similarity scores.",
    labelnames=["migration_id", "kind"],  # "avg", "min", "max"
)

migration_snapshots_created_total = Counter(
    "migration_snapshots_created_total",
    "Total Qdrant snapshots created for migrations.",
    labelnames=["collection"],
)

migration_rollbacks_total = Counter(
    "migration_rollbacks_total",
    "Total migration rollbacks performed.",
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
    "search_events_inserted_total",
    "search_events_clicks_total",
    "search_events_partitions_archived_total",
    "search_events_partitions_dropped_total",
    "search_events_rows_total",
    "ab_experiments_active",
    "ab_assignments_total",
    "ab_significance_checked_total",
    "mmr_diversify_total",
    "mmr_diversity_score",
    "mmr_lambda_param",
    # Adaptive K metrics (PR-10)
    "adaptive_k_used_total",
    "adaptive_k_lexical",
    "adaptive_k_vector",
    # Migration framework metrics (PR-07)
    "migration_backfill_progress",
    "migration_backfill_speed",
    "migration_canary_similarity",
    "migration_snapshots_created_total",
    "migration_rollbacks_total",
    # OpenTelemetry tracing metrics
    "otel_spans_exported_total",
    "otel_export_errors_total",
    # Shadow traffic metrics (PR-08)
    "shadow_traffic_total",
    "shadow_traffic_errors_total", 
    "shadow_traffic_latency_ms",
]


# ---------------------------------------------------------------------------
# Custom OTel Span Exporter with Prometheus metrics
# ---------------------------------------------------------------------------
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class CountingSpanExporter(SpanExporter):
    """OTel SpanExporter that wraps another exporter and increments Prometheus counters.
    
    This exporter tracks successful exports and errors in Prometheus metrics
    while delegating actual export to the wrapped exporter (e.g., OTLPExporter).
    """

    def __init__(self, wrapped_exporter: SpanExporter, job_name: str = "oriahs"):
        self.wrapped_exporter = wrapped_exporter
        self.job_name = job_name

    def export(self, spans: list) -> SpanExportResult:
        """Export spans and increment metrics."""
        try:
            # Delegate to wrapped exporter
            result = self.wrapped_exporter.export(spans)

            # Increment success counter
            otel_spans_exported_total.labels(job=self.job_name).inc(len(spans))

            return result
        except Exception as exc:
            # Increment error counter
            error_type = type(exc).__name__
            otel_export_errors_total.labels(job=self.job_name, error_type=error_type).inc()
            raise

    def shutdown(self) -> None:
        """Shutdown the wrapped exporter."""
        self.wrapped_exporter.shutdown()


# ---------------------------------------------------------------------------
# Shadow traffic metrics (PR-08)
# ---------------------------------------------------------------------------
shadow_traffic_total = Counter(
    "shadow_traffic_total",
    "Total number of shadow traffic executions",
    ["treatment_strategy", "experiment_id"],
)
shadow_traffic_errors_total = Counter(
    "shadow_traffic_errors_total", 
    "Total number of shadow traffic errors",
    ["reason", "experiment_id"],
)
shadow_traffic_latency_ms = Histogram(
    "shadow_traffic_latency_ms",
    "Shadow traffic execution latency in milliseconds",
    ["experiment_id"],
)
