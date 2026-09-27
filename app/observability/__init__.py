"""Observability module (ARCHITECT §11, P-15 full set, C-12 Critical additions).

Provides:
- ``metrics.py`` — all 22 Prometheus collectors (7 MVP + 15 Critical), including:
  - Search latency, indexing lag, cache metrics (MVP)
  - Reranker latency, circuit breaker, fusion strategy, push-down, throttle, eval metrics (Critical)
- ``logging.py`` — structured JSON logger with request_id/tenant_id/trace_id.
- ``health.py`` — readiness probes for PG, Qdrant, Redis and outbox lag.
"""
