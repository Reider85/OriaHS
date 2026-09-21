"""Health + readiness endpoints (ARCHITECT §14.5, P-12).

``/health/live``  — process liveness, no dependency I/O (<1ms).
``/health/ready`` — PostgreSQL + Qdrant + Redis probes and an outbox-backlog
grade. Any ``fail`` yields 503; ``outbox_lag=degraded`` stays 200.

``/metrics`` is exposed by ``prometheus-fastapi-instrumentator`` in
``app.main.create_app`` and is intentionally not declared here.
"""

from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Response, status
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.redis_client import get_redis_client
from app.db.session import get_session
from app.observability.health import HealthChecker
from app.search.qdrant_client import get_async_qdrant_client

router = APIRouter(tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis_client)]
QdrantDep = Annotated[AsyncQdrantClient, Depends(get_async_qdrant_client)]


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness probe: the process is running."""
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready(
    session: SessionDep,
    redis_client: RedisDep,
    qdrant_client: QdrantDep,
    response: Response,
) -> dict[str, Any]:
    """Readiness probe: all dependencies reachable and outbox backlog bounded."""
    checker = HealthChecker(
        session=session,
        redis_client=redis_client,
        qdrant_client=qdrant_client,
        qdrant_collection=settings.qdrant.collection,
    )
    checks = await checker.run_all()
    overall = checker.overall_status(checks)

    if overall != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": overall, "checks": checks}
