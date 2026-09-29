"""Adaptive throttle for outbox overflow (ARCHITECT §4.6, ROADMAP §4.2.10).

Implements TRIZ Principle 21 (Проскочить): when outbox pending count exceeds 50k,
API responds 202 Accepted instead of blocking, allowing continued writes while
signaling delay to clients. At 100k triggers full reindex stub.
"""

import asyncio
import time
from typing import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ThrottleConfig
from app.db.queries.outbox import count_all_pending
from app.observability import metrics
from app.observability.logging import get_logger, tenant_id_ctx

logger = get_logger(__name__)


class OutboxThrottle:
    """Manages outbox-based throttling with Redis-cached pending count."""

    def __init__(
        self,
        session_factory,
        redis_client: aioredis.Redis,
        config: ThrottleConfig,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._config = config
        self._lock = asyncio.Lock()

    async def get_pending_count(self) -> int:
        """Get cached pending count with 5s TTL (ARCHITECT §4.6).
        
        Counts pending, failed, and in_progress items due within next minute.
        Updates Prometheus gauge `outbox_pending_count`.
        """
        cache_key = "throttle:pending_count"
        
        # Try Redis cache first. Redis - ускоритель, а не источник правды:
        # при падении соединения ронять каждый /index было нельзя (C-11),
        # поэтому кэш оборачиваем в try/except и всегда падаем в БД.
        try:
            cached_value = await self._redis.get(cache_key)
        except Exception as exc:  # noqa: BLE001 — degrade to DB on any cache error
            logger.warning("Throttle cache read failed, falling back to DB", extra={"error": str(exc)})
            cached_value = None
        
        if cached_value is not None:
            count = int(cached_value)
            metrics.outbox_pending_count.set(count)
            return count

        # Cache miss: query database
        async with self._session_factory() as session:
            count = await count_all_pending(session)
        
        # Store in Redis with 5s TTL (best effort: кэш, а не критичный путь)
        try:
            await self._redis.setex(cache_key, 5, count)
        except Exception as exc:  # noqa: BLE001 — cache write failure is not fatal
            logger.warning("Throttle cache write failed", extra={"error": str(exc)})
        
        metrics.outbox_pending_count.set(count)
        
        return count

    async def should_throttle(self) -> bool:
        """Return True if pending count exceeds warning threshold (50k)."""
        pending_count = await self.get_pending_count()
        return pending_count > self._config.pending_warn_threshold

    async def should_reindex(self) -> bool:
        """Return True if pending count exceeds reindex threshold (100k)."""
        pending_count = await self.get_pending_count()
        return pending_count > self._config.pending_reindex_threshold

    async def check_and_log_reindex_trigger(self) -> bool:
        """Check if reindex should be triggered and log critical warning.
        
        Returns True if reindex was triggered, False otherwise.
        """
        if await self.should_reindex():
            logger.critical(
                "Outbox overflow - full reindex triggered",
                extra={
                    "pending_count": await self.get_pending_count(),
                    "threshold": self._config.pending_reindex_threshold,
                },
            )
            metrics.outbox_reindex_triggered_total.inc()
            return True
        return False

    async def get_throttle_status(self) -> str:
        """Get current throttle status for response."""
        pending_count = await self.get_pending_count()
        
        if pending_count > self._config.pending_reindex_threshold:
            return "reindex"
        elif pending_count > self._config.pending_warn_threshold:
            return "throttled"
        else:
            return "normal"


__all__ = ["OutboxThrottle"]