"""Prometheus metric registry (P-12 skeleton; full set in P-15).

``index_lag_seconds`` (P-12) plus the reconciler's ``dead_letters_total``
counter (P-13, ARCHITECT §4.4 step 6) live here so ``/metrics`` exposes them
from day one. The remaining MVP metrics — search latency histogram, Qdrant
error counter and embedding-cache counters — arrive in P-15.

Defining collectors at module scope means they register with the default
registry exactly once per process (import caching), which avoids duplicate
registration errors when ``create_app()`` runs several times in tests.
"""

from prometheus_client import Counter, Gauge

index_lag_seconds = Gauge(
    "index_lag_seconds",
    "Seconds since the oldest pending/failed search_outbox row was created.",
)

dead_letters_total = Counter(
    "dead_letters_total",
    "Total search_outbox rows moved to the 'dead' state after max_attempts.",
)

__all__ = ["index_lag_seconds", "dead_letters_total"]
