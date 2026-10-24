"""Daily tenant statistics refresh for adaptive K (PR-10, ARCHITECT §6.4).

For every tenant with at least one live document, recomputes
``doc_count = COUNT(*) FROM documents WHERE tenant_id = … AND deleted_at IS NULL``
and upserts it into ``tenant_search_stats``. ``avg_query_len`` /
``total_searches`` are maintained online by the search path
(``tenant_stats.update_avg_query_len``) and must not be touched here.

Designed for daily cron execution (e.g. 01:30 UTC, after partition management).

Usage:
    python scripts/update_tenant_stats.py

Exit codes:
    0 - Success (including "no tenants")
    1 - Update failed
"""

import asyncio
import logging
import sys
from uuid import UUID

from sqlalchemy import text

from app.db.queries import tenant_stats as tenant_stats_queries
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def main() -> int:
    """Recompute doc_count for every tenant and upsert into tenant_search_stats."""
    try:
        logger.info("Starting tenant stats update (PR-10)")
        updated = 0

        async with async_session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT tenant_id, COUNT(*) AS cnt "
                    "FROM documents "
                    "WHERE deleted_at IS NULL "
                    "GROUP BY tenant_id"
                )
            )
            rows = result.fetchall()
            logger.info("Found %d tenants with live documents", len(rows))

            for row in rows:
                tenant_id: UUID = row.tenant_id
                count: int = int(row.cnt)
                await tenant_stats_queries.update_doc_count(
                    session, tenant_id, count
                )
                logger.debug(
                    "Updated doc_count",
                    extra={"tenant_id": str(tenant_id), "doc_count": count},
                )
                updated += 1

            await session.commit()

        logger.info("Tenant stats update complete: %d tenants refreshed", updated)
        return 0

    except Exception as exc:
        logger.error("Tenant stats update failed: %s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    sys.exit(asyncio.run(main()))
