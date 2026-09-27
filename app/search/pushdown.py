"""Push-down filter logic (C-10, ARCHITECT §8.2, ROADMAP §4.2.9).

For highly selective filters (``selectivity_estimate < 0.1``) we first
resolve the matching ``doc_id`` list in PostgreSQL — which has GIN indexes
on ``attributes``, ``tags``, and ``tsv`` — then pass it to Qdrant as a
``MatchAny`` filter on ``doc_id``.  This bypasses HNSW traversal and
reduces vector search latency by 3–5x on selective filters.

The heuristic uses ``EXPLAIN (FORMAT JSON)`` to estimate the expected row
count and ``pg_class.reltuples`` (Redis-cached) for the total tenant row
count.  When the selectivity ratio falls below the configured threshold
the push-down is applied; otherwise the standard Qdrant payload-filter path
is used.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PushdownConfig, settings
from app.db.queries.tenants import get_tenant_doc_count
from app.search.filters import SearchFilters

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PushdownDecision:
    """Outcome of the push-down eligibility check."""

    use_pushdown: bool
    doc_ids: list[UUID] = field(default_factory=list)
    selectivity: float | None = None
    reason: str = ""


def _build_pushdown_where(
    tenant_id: UUID,
    filters: SearchFilters,
) -> tuple[list[str], dict[str, Any]]:
    """Build SQL WHERE clauses from ``SearchFilters`` (duplicated from lexical.py).

    Returns ``(clauses, params)`` where *clauses* are SQL fragment strings
    and *params* is a dict of bind-parameter values.  The ``tenant_id`` and
    ``deleted_at IS NULL`` conditions are always included.
    """
    clauses: list[str] = [
        "tenant_id = :tenant_id",
        "deleted_at IS NULL",
    ]
    params: dict[str, Any] = {"tenant_id": str(tenant_id)}

    if filters.language:
        clauses.append("language = ANY(:languages)")
        params["languages"] = filters.language

    if filters.tags_any:
        clauses.append("tags && :tags")
        params["tags"] = filters.tags_any

    attrs = filters.attributes or {}
    category = attrs.get("category")
    if category is not None:
        clauses.append("attributes->>'category' = :category")
        params["category"] = str(category)

    price = attrs.get("price")
    if isinstance(price, dict):
        if price.get("gte") is not None:
            clauses.append("CAST(attributes->>'price' AS float) >= :price_gte")
            params["price_gte"] = float(price["gte"])
        if price.get("lte") is not None:
            clauses.append("CAST(attributes->>'price' AS float) <= :price_lte")
            params["price_lte"] = float(price["lte"])

    if filters.created_after is not None:
        clauses.append("created_at >= :created_after")
        params["created_after"] = filters.created_after

    return clauses, params


def _has_selective_filters(filters: SearchFilters) -> bool:
    """Return True if any filter that benefits from push-down is active.

    ``model_name`` is excluded — it is already efficient via Qdrant payload
    filter (indexed field, low cardinality).
    """
    if filters.language:
        return True
    if filters.tags_any:
        return True
    if filters.attributes:
        return True
    if filters.created_after is not None:
        return True
    return False


async def _estimate_selectivity(
    session: AsyncSession,
    tenant_id: UUID,
    filters: SearchFilters,
    total_rows: int,
) -> float:
    """Estimate filter selectivity using ``EXPLAIN (FORMAT JSON)``.

    Returns a ratio in ``[0.0, 1.0]`` where lower means more selective.
    """
    if total_rows <= 0:
        return 1.0

    clauses, params = _build_pushdown_where(tenant_id, filters)
    where_sql = " AND ".join(clauses)

    explain_sql = (
        f"EXPLAIN (FORMAT JSON) SELECT id FROM documents WHERE {where_sql} LIMIT 5000"
    )

    result = await session.execute(text(explain_sql), params)
    row = result.fetchone()
    if not row:
        return 1.0

    plan = row[0]
    if isinstance(plan, str):
        plan = json.loads(plan)

    # ``Plan.Rows`` is the planner's estimate of output rows
    try:
        estimated_rows = plan[0]["Plan"]["Rows"]
    except (KeyError, IndexError, TypeError):
        return 1.0

    return min(float(estimated_rows) / total_rows, 1.0)


async def _fetch_filtered_doc_ids(
    session: AsyncSession,
    tenant_id: UUID,
    filters: SearchFilters,
    limit: int,
) -> list[UUID]:
    """Fetch matching doc_ids from Postgres with the given filters."""
    clauses, params = _build_pushdown_where(tenant_id, filters)
    where_sql = " AND ".join(clauses)
    params["limit"] = limit

    stmt = text(
        f"SELECT id FROM documents WHERE {where_sql} LIMIT :limit"
    )

    result = await session.execute(stmt, params)
    return [row[0] for row in result.fetchall()]


async def maybe_pushdown(
    session: AsyncSession,
    redis_client: aioredis.Redis,
    tenant_id: UUID,
    filters: SearchFilters,
    config: PushdownConfig | None = None,
) -> PushdownDecision:
    """Decide whether to push-down filters to Postgres before Qdrant.

    Returns a ``PushdownDecision`` indicating whether to use push-down,
    the matching ``doc_ids`` (if applicable), and diagnostic metadata.

    Push-down is skipped when:
    - The feature flag ``pushdown_enabled`` is ``False``.
    - No meaningful filters are present (selectivity ≈ 1.0).
    - The resulting ``doc_ids`` list exceeds ``max_candidate_ids``.
    """
    config = config or settings.pushdown

    if not settings.feature_flags.pushdown_enabled:
        return PushdownDecision(use_pushdown=False, reason="disabled")

    if not _has_selective_filters(filters):
        return PushdownDecision(use_pushdown=False, reason="no_filters")

    total_rows = await get_tenant_doc_count(session, redis_client, tenant_id)
    selectivity = await _estimate_selectivity(session, tenant_id, filters, total_rows)

    logger.debug(
        "Push-down selectivity estimate",
        extra={"selectivity": selectivity, "total_rows": total_rows},
    )

    if selectivity >= config.selectivity_threshold:
        return PushdownDecision(
            use_pushdown=False,
            selectivity=selectivity,
            reason="not_selective",
        )

    doc_ids = await _fetch_filtered_doc_ids(
        session, tenant_id, filters, limit=config.max_candidate_ids + 1
    )

    if len(doc_ids) > config.max_candidate_ids:
        return PushdownDecision(
            use_pushdown=False,
            selectivity=selectivity,
            reason="too_large",
        )

    return PushdownDecision(
        use_pushdown=True,
        doc_ids=doc_ids,
        selectivity=selectivity,
        reason="used",
    )
