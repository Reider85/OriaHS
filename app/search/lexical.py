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

from app.config import settings
from app.db.models.document import Document
from app.search.exceptions import DeadlockError
from app.search.filters import SearchFilters
from app.search.langid_fasttext import detect_token_languages, normalize_query, translit_variants

SNIPPET_LENGTH = 200
STATEMENT_TIMEOUT_MS = 100
# Blended scoring weights from ARCHITECT §6.1: ts_rank × 0.7 + similarity × 0.3.
TS_RANK_WEIGHT = 0.7
TRIGRAM_WEIGHT = 0.3
TRIGRAM_MATCH_THRESHOLD = 0.3


def _build_mixed_tsquery(latin_tokens: list[str], cyrillic_tokens: list[str]) -> text:
    """Build tsquery for mixed language search with transliteration.

    Args:
        latin_tokens: List of tokens with Latin script
        cyrillic_tokens: List of tokens with Cyrillic script

    Returns:
        SQL text fragment with tsquery
    """
    # Build subqueries for each script - use spaces for single script, AND for mixed
    latin_subquery = " ".join(latin_tokens) if latin_tokens else ""
    cyrillic_subquery = " ".join(cyrillic_tokens) if cyrillic_tokens else ""

    # Build main query with transliteration variants
    main_queries = []

    # Original query
    if latin_subquery and cyrillic_subquery:
        main_queries.append(f"({latin_subquery}) & ({cyrillic_subquery})")
    elif latin_subquery:
        main_queries.append(latin_subquery)
    elif cyrillic_subquery:
        main_queries.append(cyrillic_subquery)

    # Transliterated variants (only for mixed scripts)
    if latin_tokens and cyrillic_tokens:
        translit_latin = " & ".join(translit_variants(" ".join(latin_tokens)))
        if translit_latin != latin_subquery:
            main_queries.append(f"({translit_latin}) & ({cyrillic_subquery})")

        translit_cyrillic = " & ".join(translit_variants(" ".join(cyrillic_tokens)))
        if translit_cyrillic != cyrillic_subquery:
            main_queries.append(f"({latin_subquery}) & ({translit_cyrillic})")

    if not main_queries:
        return text("''")

    # Combine with OR for transliteration variants
    tsquery = " | ".join(main_queries)
    return text(f"to_tsquery('simple', '{tsquery}')")


def _is_mixed_query(query: str) -> bool:
    """Check if query contains both Latin and Cyrillic scripts."""
    tokens = detect_token_languages(query)
    return bool(tokens["latin"] and tokens["cyrillic"])


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
            # Normalize query and check for mixed language
            normalized_query = normalize_query(query)
            mixed_language = settings.feature_flags.mixed_language_enabled and _is_mixed_query(normalized_query)

            if mixed_language:
                # Build mixed tsquery with transliteration
                tokens = detect_token_languages(normalized_query)
                tsq = _build_mixed_tsquery(tokens["latin"], tokens["cyrillic"])

                # Log mixed query detection and transliteration usage
                from app.observability import metrics
                metrics.mixed_queries_detected_total.inc()

                # Check if transliteration was actually used
                if any([
                    translit_variants(" ".join(tokens["latin"])) != [" ".join(tokens["latin"])],
                    translit_variants(" ".join(tokens["cyrillic"])) != [" ".join(tokens["cyrillic"])],
                ]):
                    metrics.transliteration_used_total.inc()
            else:
                # Use standard tsquery
                tsq = func.plainto_tsquery("simple", normalized_query)

            trigram = func.similarity(Document.title + " " + Document.content, normalized_query)
            ts_rank = func.ts_rank(Document.tsv, tsq)
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
            last_error = DeadlockError(
                f"PG deadlock detected, retry {attempt + 1}/{max_retries}: {exc}"
            )
            if attempt < max_retries:
                delay = base_delay_ms * (2**attempt) / 1000.0  # Exponential backoff
                await asyncio.sleep(delay)
                continue
            raise last_error from None

        except Exception as exc:
            # Re-raise non-deadlock errors immediately
            raise exc

    # This should never be reached, but add a return for type safety
    return []
