"""Observability module (ARCHITECT §11, P-15 full set).

Provides:
- ``metrics.py`` — all 7 MVP Prometheus collectors (histogram, counters, gauges).
- ``logging.py`` — structured JSON logger with request_id/tenant_id/trace_id.
- ``health.py`` — readiness probes for PG, Qdrant, Redis and outbox lag.
"""
