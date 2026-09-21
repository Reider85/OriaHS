"""Query helpers for the ``search_outbox_dead_digest`` table (ARCHITECT §4.4, P-14).

The digest worker runs hourly and aggregates ``dead`` outbox rows grouped by
``(tenant_id, model_name, error_type)`` into a small "health map". Error
types are classified with simple ILIKE checks — no regex on MVP
(ROADMAP §3.2.5). ``ON CONFLICT DO UPDATE`` keeps the digest cumulative and
bounds the sample arrays to the 10 most recent doc ids / errors.
"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_SAMPLES = 10

_DIGEST_SQL = text(
    f"""
    INSERT INTO search_outbox_dead_digest
        (tenant_id, model_name, error_type, count,
         first_seen_at, last_seen_at, sample_doc_ids, sample_errors)
    SELECT
        d.tenant_id,
        d.embedding_model AS model_name,
        CASE
            WHEN o.last_error ILIKE '%qdrant%' OR o.last_error ILIKE '%timeout%'
                THEN 'qdrant_unavailable'
            WHEN o.last_error ILIKE '%embedding%' OR o.last_error ILIKE '%model%'
                THEN 'embedding_failed'
            WHEN o.last_error ILIKE '%payload%' OR o.last_error ILIKE '%validation%'
                THEN 'payload_invalid'
            ELSE 'other'
        END AS error_type,
        count(*) AS count,
        min(o.updated_at) AS first_seen_at,
        max(o.updated_at) AS last_seen_at,
        (array_agg(d.id ORDER BY o.updated_at DESC))[1:{_MAX_SAMPLES}] AS sample_doc_ids,
        (array_agg(o.last_error ORDER BY o.updated_at DESC))[1:{_MAX_SAMPLES}] AS sample_errors
    FROM search_outbox o
    JOIN documents d ON d.id = o.document_id
    WHERE o.status = 'dead'
      AND o.updated_at > :since
    GROUP BY d.tenant_id, d.embedding_model, error_type
    ON CONFLICT (tenant_id, model_name, error_type)
    DO UPDATE SET
        count = search_outbox_dead_digest.count + EXCLUDED.count,
        last_seen_at = EXCLUDED.last_seen_at,
        sample_doc_ids =
            (array_cat(search_outbox_dead_digest.sample_doc_ids,
                       EXCLUDED.sample_doc_ids))[1:{_MAX_SAMPLES}],
        sample_errors =
            (array_cat(search_outbox_dead_digest.sample_errors,
                       EXCLUDED.sample_errors))[1:{_MAX_SAMPLES}]
    """
)


async def aggregate_dead_letters(session: AsyncSession, since: datetime) -> int:
    """Aggregate ``dead`` outbox rows newer than ``since`` into the digest.

    Returns the number of digest rows inserted or updated (the group count).
    Each row's ``updated_at`` marks the moment it went ``dead``, so re-running
    the digest without new dead rows is a no-op.
    """
    result = await session.execute(_DIGEST_SQL, {"since": since})
    return cast(CursorResult[Any], result).rowcount or 0


__all__ = ["aggregate_dead_letters"]
