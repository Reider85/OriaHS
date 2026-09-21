"""Outbox reconciler (ARCHITECT §4.4, ROADMAP §3.2.5; P-13) + digest (P-14).

The reconciler is the safety net of the dual-write pattern: it scans
``search_outbox`` for pending/failed rows every 30 seconds, syncs them to
Qdrant, and applies exponential backoff before eventually moving permanent
failures to the ``dead`` state (TRIZ-gate). The digest worker (P-14)
aggregates ``dead`` rows hourly into ``search_outbox_dead_digest``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.reconciler.digest import DigestWorker as DigestWorker
    from app.reconciler.worker import ReconcilerWorker as ReconcilerWorker

__all__ = ["DigestWorker", "ReconcilerWorker"]


def __getattr__(name: str) -> object:
    # Lazily re-export the worker classes so ``python -m
    # app.reconciler.worker`` / ``python -m app.reconciler.digest`` do not
    # import the module twice (once via this package, once as ``__main__``),
    # which runpy flags as unpredictable.
    if name == "ReconcilerWorker":
        from app.reconciler.worker import ReconcilerWorker

        return ReconcilerWorker
    if name == "DigestWorker":
        from app.reconciler.digest import DigestWorker

        return DigestWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
