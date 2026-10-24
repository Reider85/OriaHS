"""Query helpers for the ``tenant_search_stats`` table (ARCHITECT §3.1, PR-10).

Per-tenant search statistics backing adaptive K selection (ARCHITECT §6.4):
``doc_count`` is refreshed daily by ``scripts/update_tenant_stats.py`` while
``avg_query_len`` / ``total_searches`` are updated atomically on every search.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.tenant_search_stat import TenantSearchStat


async def get(session: AsyncSession, tenant_id: UUID) -> TenantSearchStat | None:
    """Fetch stats for a tenant, returns None if the tenant has no row yet.

    The read path (search) must never insert — rows are created by
    ``get_or_create`` (cron / on-demand) to keep searches insert-free.
    """
    result = await session.execute(
        text(
            "SELECT tenant_id, doc_count, avg_query_len, total_searches, last_updated "
            "FROM tenant_search_stats WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": tenant_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return TenantSearchStat(
        tenant_id=row.tenant_id,
        doc_count=row.doc_count,
        avg_query_len=row.avg_query_len,
        total_searches=row.total_searches,
        last_updated=row.last_updated,
    )


async def get_or_create(session: AsyncSession, tenant_id: UUID) -> TenantSearchStat:
    """Return the tenant's stats row, creating a default one if missing."""
    stat = await get(session, tenant_id)
    if stat is not None:
        return stat

    await session.execute(
        text(
            """
            INSERT INTO tenant_search_stats
                (tenant_id, doc_count, avg_query_len, total_searches, last_updated)
            VALUES (:tenant_id, 0, 0, 0, now())
            ON CONFLICT (tenant_id) DO NOTHING
            """
        ),
        {"tenant_id": tenant_id},
    )
    stat = await get(session, tenant_id)
    assert stat is not None  # row just created (or won the race)
    return stat


async def update_doc_count(session: AsyncSession, tenant_id: UUID, count: int) -> None:
    """Upsert ``doc_count`` for a tenant (daily cron, ARCHITECT §6.4)."""
    await session.execute(
        text(
            """
            INSERT INTO tenant_search_stats (tenant_id, doc_count, last_updated)
            VALUES (:tenant_id, :count, now())
            ON CONFLICT (tenant_id) DO UPDATE
            SET doc_count = :count, last_updated = now()
            """
        ),
        {"tenant_id": tenant_id, "count": count},
    )


async def update_avg_query_len(
    session: AsyncSession, tenant_id: UUID, query_len: int
) -> None:
    """Atomically update the running average query length and search counter.

    Single-statement upsert implementing the running average
    ``new_avg = (old_avg * n_old + query_len) / (n_old + 1)`` where
    ``n_old = total_searches`` before this search (PROD-PROMPTS PR-10).
    Safe for concurrent searches — no read-modify-write round-trip.
    """
    await session.execute(
        text(
            """
            INSERT INTO tenant_search_stats (tenant_id, avg_query_len, total_searches, last_updated)
            VALUES (:tenant_id, :query_len, 1, now())
            ON CONFLICT (tenant_id) DO UPDATE SET
                avg_query_len = CASE
                    WHEN tenant_search_stats.total_searches = 0 THEN :query_len
                    ELSE (tenant_search_stats.avg_query_len * tenant_search_stats.total_searches
                          + :query_len)
                         / (tenant_search_stats.total_searches + 1)
                END,
                total_searches = tenant_search_stats.total_searches + 1,
                last_updated = now()
            """
        ),
        {"tenant_id": tenant_id, "query_len": query_len},
    )


async def increment_search_count(session: AsyncSession, tenant_id: UUID) -> None:
    """Increment ``total_searches`` without touching the average."""
    await session.execute(
        text(
            """
            INSERT INTO tenant_search_stats (tenant_id, total_searches, last_updated)
            VALUES (:tenant_id, 1, now())
            ON CONFLICT (tenant_id) DO UPDATE
            SET total_searches = tenant_search_stats.total_searches + 1, last_updated = now()
            """
        ),
        {"tenant_id": tenant_id},
    )
