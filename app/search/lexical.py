"""Lexical search channel (P-09, ARCHITECT §6.1, §8.1).

Half of hybrid search: PostgreSQL full-text ranking (``ts_rank`` against the
GENERATED ``tsv`` column) blended with ``pg_trgm`` similarity for fuzzy
matching. Returns the top ``K_lexical`` candidates that P-11 feeds into RRF
fusion.

Filters are applied as SQL pre-filters (ARCHITECT §8.1) — never post-filtered.
A per-query ``SET LOCAL statement_timeout`` bounds worst-case latency so a
pathological query cannot stall the read path.
"""

import asyncio
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel
from sqlalchemy import Float, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.search.exceptions import DeadlockError, StatementTimeoutError
from app.search.filters import SearchFilters

SNIPPET_LENGTH = 200
STATEMENT_TIMEOUT_MS = 100
# Blended scoring weights from ARCHITECT §6.1: ts_rank × 0.7 + similarity × 0.3.
TS_RANK_WEIGHT = 0.7
TRIGRAM_WEIGHT = 0.3
TRIGRAM_MATCH_THRESHOLD = 0.3


class LexicalHit(BaseModel):
    """One candidate from the PostgreSQL lexical channel."""

    doc_id: UUID
    score: float
    title: str
    content_snippet: str  # leading 200 chars of ``content``, no highlight (Critical)
    source: Literal["lexical"] = "lexical"


async def lexical_search(
    session: AsyncSession,
    query: str,
    tenant_id: UUID,
    filters: SearchFilters,
    k: int = 50,
) -> list[LexicalHit]:
    """Run the lexical channel: FTS + trigram scoring under tenant isolation.

    ``statement_timeout`` is applied per-transaction and surfaces as a
    ``QueryCanceledError`` (asyncpg) if the query exceeds the budget.
    
    Implements deadlock retry with exponential backoff (C-09).
    """
    await session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
    
    # Deadlock retry logic
    max_retries = 2
    base_delay_ms = 50
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            tsq = func.plainto_tsquery("simple", query)
            ts_rank = func.ts_rank(Document.tsv, tsq)
            trigram = func.similarity(Document.title + " " + Document.content, query)
            score = (ts_rank * TS_RANK_WEIGHT + trigram * TRIGRAM_WEIGHT).label("score")

            conditions = [
                Document.tenant_id == tenant_id,
                Document.deleted_at.is_(None),
                or_(
                    Document.tsv.op("@@")(tsq),
                    trigram > TRIGRAM_MATCH_THRESHOLD,
                ),
            ]

            if filters.language:
                conditions.append(Document.language.in_(filters.language))
            if filters.tags_any:
                conditions.append(Document.tags.op("&&")(filters.tags_any))

            attrs = filters.attributes or {}
            category = attrs.get("category")
            if category is not None:
                conditions.append(Document.attributes["category"].astext == str(category))

            price = attrs.get("price")
            if isinstance(price, dict):
                if price.get("gte") is not None:
                    conditions.append(
                        cast(Document.attributes["price"].astext, Float) >= float(price["gte"])
                    )
                if price.get("lte") is not None:
                    conditions.append(
                        cast(Document.attributes["price"].astext, Float) <= float(price["lte"])
                    )

            if filters.created_after is not None:
                conditions.append(Document.created_at >= filters.created_after)

            stmt = (
                select(
                    Document.id,
                    Document.title,
                    func.left(Document.content, SNIPPET_LENGTH).label("content_snippet"),
                    score,
                )
                .where(*conditions)
                .order_by(score.desc())
                .limit(k)
            )

            rows = (await session.execute(stmt)).all()
            return [
                LexicalHit(
                    doc_id=row.id,
                    score=float(row.score),
                    title=row.title,
                    content_snippet=row.content_snippet,
                )
                for row in rows
]
            
        except asyncpg.exceptions.DeadlockDetectedError as exc:
            last_error = DeadlockError(f"PG deadlock detected, retry {attempt + 1}/{max_retries}: {exc}")
            if attempt < max_retries:
                delay = base_delay_ms * (2 ** attempt) / 1000.0  # Exponential backoff
                await asyncio.sleep(delay)
                continue
            raise last_error
            
        except Exception as exc:
            # Re-raise non-deadlock errors immediately
            raise exc
