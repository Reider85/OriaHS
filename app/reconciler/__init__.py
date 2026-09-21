"""Outbox reconciler (ARCHITECT §4.4, ROADMAP §3.2.5; P-13).

The reconciler is the safety net of the dual-write pattern: it scans
``search_outbox`` for pending/failed rows every 30 seconds, syncs them to
Qdrant, and applies exponential backoff before eventually moving permanent
failures to the ``dead`` state (TRIZ-gate). P-14 adds the digest worker that
aggregates ``dead`` rows into a health map.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.reconciler.worker import ReconcilerWorker as ReconcilerWorker

__all__ = ["ReconcilerWorker"]


def __getattr__(name: str) -> object:
    # Lazily re-export ``ReconcilerWorker`` so ``python -m
    # app.reconciler.worker`` does not import the module twice (once via this
    # package, once as ``__main__``), which runpy flags as unpredictable.
    if name == "ReconcilerWorker":
        from app.reconciler.worker import ReconcilerWorker

        return ReconcilerWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
