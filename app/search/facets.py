"""Facet aggregation for search results (C-06, ARCHITECT §8.4).

Facets are computed on the top-N fusion results, not the entire collection — this
is both faster and gives user-meaningful numbers (relevant categories for the
current query, not "how many exist in the whole database").

Supports two field types:
- `tags` (array of strings) → counts of individual tags
- `attributes.category` (JSONB string) → counts of category values

Timeout: 50ms per facet query to avoid blocking the main search response.
"""

import logging
from uuid import UUID

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FacetBucket

logger = logging.getLogger(__name__)

# Default fields to compute facets for when not specified
DEFAULT_FACET_FIELDS = ["tags", "attributes.category"]


async def compute_facets(
    doc_ids: list[UUID],
    session: AsyncSession,
    facet_fields: list[str] | None = None,
    top_n: int = 20,
) -> dict[str, list[FacetBucket]]:
    """Compute facet aggregations on a filtered set of documents.

    Parameters
    ----------
    doc_ids:
        Document IDs to aggregate over (typically the top-K from fusion).
    session:
        Async SQLAlchemy session for database queries.
    facet_fields:
        List of field names to facet on. If None, returns empty dict.
        Supported: "tags", "attributes.category".
    top_n:
        Maximum number of buckets to return per field.

    Returns
    -------
    dict mapping field name to list of FacetBucket, sorted by count descending.
    Returns empty dict if doc_ids is empty or facet_fields is None.

    A field whose query succeeded but matched nothing is present with an empty
    list; a field whose query failed is omitted entirely (None from the
    _compute_* helpers), so clients can tell "no matches" from "unknown".
    """
    if not doc_ids or not facet_fields:
        return {}

    results: dict[str, list[FacetBucket]] = {}

    for field in facet_fields:
        if field == "tags":
            buckets = await _compute_tag_facets(doc_ids, session, top_n)
        elif field == "attributes.category":
            buckets = await _compute_category_facets(doc_ids, session, top_n)
        else:
            logger.warning(f"Unsupported facet field: {field}")
            continue

        if buckets is not None:
            # SQL уже ограничивает через LIMIT, но режем и здесь: контракт
            # top_n не должен зависеть от того, какой БД под капотом.
            results[field] = buckets[:top_n]

    return results


async def _compute_tag_facets(
    doc_ids: list[UUID], session: AsyncSession, top_n: int
) -> list[FacetBucket] | None:
    """Count individual tags from the documents array field."""
    try:
        # Set statement timeout to avoid blocking the main response
        await session.execute(text("SET LOCAL statement_timeout = '50ms'"))

        # func.array(..., type_=...) не существует: любой type_/kwargs уходит
        # в generic Function и роняет построение запроса. unnesting делаем
        # через unnest() + явный cast пустого массива, чтобы документы без
        # тегов не исчезали из GROUP BY.
        tag = func.unnest(func.coalesce(Document.tags, cast(text("'{}'"), ARRAY(TEXT)))).label(
            "tag"
        )

        stmt = (
            select(
                tag,
                func.count().label("count"),
            )
            .where(
                Document.id.in_(doc_ids),
                Document.deleted_at.is_(None),
                Document.tags.isnot(None),
            )
            .group_by(tag)
            .order_by(func.count().desc())
            .limit(top_n)
        )

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [FacetBucket(value=row.tag, count=row.count) for row in rows]

    except Exception as exc:
        logger.warning("Tag facets computation failed, returning empty", extra={"error": str(exc)})
        return None


async def _compute_category_facets(
    doc_ids: list[UUID], session: AsyncSession, top_n: int
) -> list[FacetBucket] | None:
    """Count category values from the documents JSONB attributes field."""
    try:
        # Set statement timeout to avoid blocking the main response
        await session.execute(text("SET LOCAL statement_timeout = '50ms'"))

        stmt = (
            select(
                func.coalesce(Document.attributes["category"].astext, "").label("category"),
                func.count().label("count"),
            )
            .where(
                Document.id.in_(doc_ids),
                Document.deleted_at.is_(None),
                Document.attributes["category"].astext.isnot(None),
            )
            .group_by(func.coalesce(Document.attributes["category"].astext, ""))
            .order_by(func.count().desc())
            .limit(top_n)
        )

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [FacetBucket(value=row.category, count=row.count) for row in rows]

    except Exception as exc:
        logger.warning(
            "Category facets computation failed, returning empty", extra={"error": str(exc)}
        )
        return None


# Import Document model at the end to avoid circular imports
from app.db.models.document import Document
