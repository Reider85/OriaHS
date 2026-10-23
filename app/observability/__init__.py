"""Observability module (ARCHITECT §11, P-15 full set, C-12 Critical additions, B-09, PR-06 tracing).

Provides:
- ``metrics.py`` — all 29 Prometheus collectors (7 MVP + 15 Critical + 3 extras + 2 B-09 + 2 OTel), including:
  - Search latency, indexing lag, cache metrics (MVP)
  - Reranker latency, circuit breaker, fusion strategy, push-down, throttle, eval metrics (Critical)
  - Extra metrics: reconciler batch size, eval runs, regression detection
  - Vector search latency by pushdown mode, nightly eval timestamp (B-09)
  - OTel tracing metrics: spans exported, export errors (PR-06)
- ``logging.py`` — structured JSON logger with request_id/tenant_id/trace_id.
- ``health.py`` — readiness probes for PG, Qdrant, Redis and outbox lag.
- ``tracing.py`` — OpenTelemetry distributed tracing with Tempo export and trace↔log correlation.
"""
