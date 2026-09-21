"""Readiness checks for ``GET /health/ready`` (ARCHITECT §14.5, P-12).

Four dependency probes: PostgreSQL (``SELECT 1``), Qdrant (collection info),
Redis (``PING``) and the pending/failed ``search_outbox`` backlog. Each probe
is isolated: an exception is reported as ``fail`` rather than propagating, so
a single dead dependency never turns ``/health/ready`` into a 500.

Outbox backlog thresholds (P-12): ``ok`` < 1000, ``degraded`` < 10000,
``fail`` otherwise. ``degraded`` keeps the service ready — it is an early
warning, not an outage.
"""

from __future__ import annotations

import logging
from typing import Literal, TypedDict

import redis.asyncio as aioredis
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "degraded", "fail"]


class HealthCheckResult(TypedDict):
    """Outcome of a single dependency probe."""

    status: CheckStatus
    message: str | None


class HealthChecker:
    """Runs the readiness probes against the injected live clients."""

    OUTBOX_OK_THRESHOLD = 1000
    OUTBOX_DEGRADED_THRESHOLD = 10000

    def __init__(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis,
        qdrant_client: AsyncQdrantClient,
        qdrant_collection: str,
    ) -> None:
        self._session = session
        self._redis = redis_client
        self._qdrant = qdrant_client
        self._qdrant_collection = qdrant_collection

    async def check_postgres(self) -> HealthCheckResult:
        """Cheap connectivity probe — ``SELECT 1`` is table-independent."""
        try:
            await self._session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - any failure means not ready
            logger.warning("Health probe failed: postgres", exc_info=exc)
            return {"status": "fail", "message": str(exc)}
        return {"status": "ok", "message": None}

    async def check_qdrant(self) -> HealthCheckResult:
        """Verify the documents collection is reachable."""
        try:
            await self._qdrant.get_collection(self._qdrant_collection)
        except Exception as exc:  # noqa: BLE001 - any failure means not ready
            logger.warning("Health probe failed: qdrant", exc_info=exc)
            return {"status": "fail", "message": str(exc)}
        return {"status": "ok", "message": None}

    async def check_redis(self) -> HealthCheckResult:
        """``PING`` the Redis cache/streams instance."""
        try:
            await self._redis.ping()
        except Exception as exc:  # noqa: BLE001 - any failure means not ready
            logger.warning("Health probe failed: redis", exc_info=exc)
            return {"status": "fail", "message": str(exc)}
        return {"status": "ok", "message": None}

    async def check_outbox_lag(self) -> HealthCheckResult:
        """Grade the pending/failed outbox backlog by size."""
        try:
            count = await self._session.scalar(
                text(
                    "SELECT count(*) FROM search_outbox "
                    "WHERE status IN ('pending','failed') AND next_retry_at <= now()"
                )
            )
        except Exception as exc:  # noqa: BLE001 - any failure means not ready
            logger.warning("Health probe failed: outbox_lag", exc_info=exc)
            return {"status": "fail", "message": str(exc)}

        backlog = int(count or 0)
        message = f"pending/failed={backlog}"
        if backlog < self.OUTBOX_OK_THRESHOLD:
            return {"status": "ok", "message": message}
        if backlog < self.OUTBOX_DEGRADED_THRESHOLD:
            return {"status": "degraded", "message": message}
        return {"status": "fail", "message": message}

    async def run_all(self) -> dict[str, HealthCheckResult]:
        """Run every probe sequentially (the session is not concurrency-safe)."""
        return {
            "postgres": await self.check_postgres(),
            "qdrant": await self.check_qdrant(),
            "redis": await self.check_redis(),
            "outbox_lag": await self.check_outbox_lag(),
        }

    @staticmethod
    def overall_status(checks: dict[str, HealthCheckResult]) -> str:
        """``not_ready`` if any probe failed, otherwise ``ready``."""
        if any(result["status"] == "fail" for result in checks.values()):
            return "not_ready"
        return "ready"
