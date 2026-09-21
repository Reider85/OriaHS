"""Prometheus metric registry (P-12 skeleton; full set in P-15).

``index_lag_seconds`` is exposed from day one so ``/metrics`` reflects the
outbox backlog. The remaining MVP metrics — search latency histogram, the
reconciler's dead-letter counter, Qdrant error counter and embedding-cache
counters — arrive in P-15.

Defining collectors at module scope means they register with the default
registry exactly once per process (import caching), which avoids duplicate
registration errors when ``create_app()`` runs several times in tests.
"""

from prometheus_client import Gauge

index_lag_seconds = Gauge(
    "index_lag_seconds",
    "Seconds since the oldest pending/failed search_outbox row was created.",
)

__all__ = ["index_lag_seconds"]
