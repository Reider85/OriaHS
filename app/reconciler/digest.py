"""Dead-letter digest worker (ARCHITECT §4.4, ROADMAP §3.2.5/§3.3; P-14).

Every ``digest_interval_minutes`` (1 h) the worker aggregates ``dead`` outbox
rows into ``search_outbox_dead_digest`` grouped by
``(tenant_id, model_name, error_type)`` — a 10–100 row "health map" built
from what would otherwise be 10k+ individually unreadable dead letters
(TRIZ-gate ROADMAP §3.3; ARCHITECT §4.7 principle 22). ``dead`` rows are
never deleted from ``search_outbox`` — they stay for audit; only the digest
table grows.

Run standalone: ``python -m app.reconciler.digest``. Graceful shutdown on
SIGTERM/SIGINT after the current aggregation.
"""

import asyncio
import logging
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ReconcilerConfig, settings
from app.db.queries import digest as digest_queries
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# Sliding window with margin ("с запасом"): rows that went dead up to 2 hours
# before the run are still picked up, so a brief worker outage (or a missed
# run) cannot silently drop a group from the digest.
_LOOKBACK_WINDOW = timedelta(hours=2)


class DigestWorker:
    """Hourly ``dead`` → ``search_outbox_dead_digest`` aggregator (§4.4)."""

    def __init__(
        self,
        config: ReconcilerConfig | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._config = config or settings.reconciler
        self._session_factory: SessionFactory = session_factory or async_session_factory
        self._stop = asyncio.Event()

    @property
    def config(self) -> ReconcilerConfig:
        """Expose the active config (used by tests and observability)."""
        return self._config

    def request_stop(self) -> None:
        """Signal the worker to exit after the current aggregation (graceful)."""
        self._stop.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.request_stop)
        except NotImplementedError:
            # asyncio cannot install loop signal handlers on Windows; the
            # KeyboardInterrupt fallback below still shuts the loop down.
            logger.warning("Loop signal handlers unavailable on this platform")

    async def run_forever(self) -> None:
        """Run the hourly digest loop until a stop is requested (ARCHITECT §4.4).

        Runs once on startup (so rows that went ``dead`` while the worker was
        down are digested without waiting a full interval), then every
        ``digest_interval_minutes``.
        """
        logger.info("Digest worker started")
        self._install_signal_handlers()
        try:
            while not self._stop.is_set():
                try:
                    await self.run_once()
                except Exception:  # noqa: BLE001 — a DB blip must not kill the loop
                    logger.exception("Digest run failed", extra={})
                await self._sleep_with_stop(self._config.digest_interval_minutes * 60)
        finally:
            logger.info("Digest worker stopped")

    async def run_once(self, since: datetime | None = None) -> int:
        """Aggregate dead rows once; returns the number of groups written.

        ``since`` defaults to ``now() - 2 hours`` (rolling window with
        margin, ARCHITECT §4.4). Tests may pass a fixed ``since`` to exercise
        the idempotency / incremental semantics deterministically.
        """
        if since is None:
            since = datetime.now(UTC) - _LOOKBACK_WINDOW
        async with self._session_factory() as session:
            groups = await digest_queries.aggregate_dead_letters(session, since)
            total_dead = await self._count_dead(session)
            await session.commit()
        logger.info(
            "Dead letter digest updated",
            extra={"groups": groups, "total_dead": total_dead},
        )
        return groups

    @staticmethod
    async def _count_dead(session: AsyncSession) -> int:
        result = await session.execute(
            text("SELECT count(*) FROM search_outbox WHERE status = 'dead'")
        )
        return int(result.scalar_one())

    async def _sleep_with_stop(self, seconds: float) -> None:
        """Sleep for ``seconds`` unless a stop was requested (interruptible)."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass


async def _main() -> None:
    worker = DigestWorker()
    await worker.run_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass

__all__ = ["DigestWorker"]
