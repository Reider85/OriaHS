"""Tenant-level query helpers for push-down filter selectivity (C-10, ARCHITECT §8.2).

Provides ``get_tenant_doc_count`` which returns the estimated row count for the
``documents`` table scoped to a tenant, cached in Redis to avoid repeated
``pg_class.reltuples`` lookups.
"""

from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_TTL = 300  # 5 minutes


async def get_tenant_doc_count(
    session: AsyncSession,
    redis_client: aioredis.Redis,
    tenant_id: UUID,
) -> int:
    """Return the estimated document count for *tenant_id*.

    Uses ``pg_class.reltuples`` (fast, approximate) cached in Redis for 5
    minutes.  Falls back to ``COUNT(*)`` with a ``statement_timeout`` if
    ``reltuples`` returns a non-positive value (e.g. after a fresh PG restore
    before the first ``ANALYZE``).
    """
    cache_key = f"pushdown:tenant_rows:{tenant_id}"

    cached = await redis_client.get(cache_key)
    if cached is not None:
        return int(cached)

    # Fast path: pg_class.reltuples estimate
    result = await session.execute(
        text("SELECT reltuples::bigint AS est FROM pg_class WHERE relname = 'documents'")
    )
    row = result.fetchone()
    est = int(row[0]) if row and row[0] else 0

    if est > 0:
        await redis_client.set(cache_key, est, ex=_CACHE_TTL)
        return est

    # Fallback: exact count (only when reltuples is stale / zero)
    await session.execute(text("SET LOCAL statement_timeout = '100ms'"))
    count_result = await session.execute(
        text("SELECT count(*) FROM documents WHERE tenant_id = :tid AND deleted_at IS NULL"),
        {"tid": str(tenant_id)},
    )
    count = int(count_result.scalar() or 0)
    await redis_client.set(cache_key, count, ex=_CACHE_TTL)
    return count
