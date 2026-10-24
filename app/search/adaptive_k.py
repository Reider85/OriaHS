"""Adaptive K parameter selection per tenant (ARCHITECT §6.4, PR-10).

Replaces the PR-00 stub. The system adapts K_lexical/K_vector per tenant
based on ``tenant_search_stats`` (doc_count) and the per-request query length:

- ``doc_count < small_tenant_doc_threshold`` (10k) -> base K = ``k_small`` (30).
- ``doc_count > large_tenant_doc_threshold`` (1M)  -> base K = ``k_large`` (100).
- otherwise -> base K = ``SearchConfig.k_lexical`` (50).
- ``len(query) < short_query_len_threshold`` (5) -> K_lexical += 20, K_vector -= 10.
- ``len(query) > long_query_len_threshold`` (20) -> K_lexical -= 10, K_vector += 20.
- Both values are clamped to [10, 200].

``doc_count`` is read through a 1-hour Redis cache so the search path does not
pay a PG round-trip per request. ``None`` (new tenant / any failure) falls back
to the static K from ``SearchConfig`` — adaptive K must never break a search.

PROD-PROMPTS PR-10 (TRIZ gate §23 «Обратная связь»).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdaptiveKConfig, SearchConfig
from app.db.models.tenant_search_stat import TenantSearchStat

logger = logging.getLogger(__name__)

#: Hard clamp bounds (PROD-PROMPTS PR-10 constraints: no K < 10 or > 200).
MIN_K = 10
MAX_K = 200

#: Short/long query deltas applied on top of the base K.
_SHORT_QUERY_K_LEXICAL_DELTA = 20
_SHORT_QUERY_K_VECTOR_DELTA = -10
_LONG_QUERY_K_LEXICAL_DELTA = -10
_LONG_QUERY_K_VECTOR_DELTA = 20

#: Redis cache for ``doc_count`` (1 hour, PROD-PROMPTS PR-10 constraint).
DOC_COUNT_CACHE_TTL_SECONDS = 3600
_DOC_COUNT_CACHE_KEY = "tenant_stats:doc_count:{tenant_id}"


@dataclass(frozen=True, slots=True)
class AdaptiveKResult:
    """Outcome of adaptive K selection for one request."""

    k_lexical: int
    k_vector: int
    bucket: str  # "small" | "medium" | "large"


def classify_bucket(doc_count: int | None, config: AdaptiveKConfig) -> str:
    """Map a tenant's ``doc_count`` to the metrics bucket label."""
    if doc_count is None:
        return "medium"
    if doc_count < config.small_tenant_doc_threshold:
        return "small"
    if doc_count > config.large_tenant_doc_threshold:
        return "large"
    return "medium"


def compute_adaptive_k(
    tenant_stats: TenantSearchStat | None,
    query: str,
    config: AdaptiveKConfig,
    *,
    default_k: int = 50,
) -> tuple[int, int]:
    """Return ``(K_lexical, K_vector)`` for a tenant + query (ARCHITECT §6.4).

    Pure function — no I/O. ``tenant_stats=None`` (new tenant) uses the default
    base K but still applies query-length adaptation. Query length is measured
    in characters (``len(query)``) — matching the PR-10 acceptance criteria.
    """
    if tenant_stats is None:
        base_k = default_k
    elif tenant_stats.doc_count < config.small_tenant_doc_threshold:
        base_k = config.k_small
    elif tenant_stats.doc_count > config.large_tenant_doc_threshold:
        base_k = config.k_large
    else:
        base_k = default_k

    query_len = len(query)
    if query_len < config.short_query_len_threshold:
        k_lexical = base_k + _SHORT_QUERY_K_LEXICAL_DELTA
        k_vector = base_k + _SHORT_QUERY_K_VECTOR_DELTA
    elif query_len > config.long_query_len_threshold:
        k_lexical = base_k + _LONG_QUERY_K_LEXICAL_DELTA
        k_vector = base_k + _LONG_QUERY_K_VECTOR_DELTA
    else:
        k_lexical = base_k
        k_vector = base_k

    k_lexical = max(MIN_K, min(MAX_K, k_lexical))
    k_vector = max(MIN_K, min(MAX_K, k_vector))
    return k_lexical, k_vector


def _stats_from_doc_count(tenant_id: UUID, doc_count: int) -> TenantSearchStat:
    """Build a stats view from a cached ``doc_count`` (no PG round-trip)."""
    return TenantSearchStat(
        tenant_id=tenant_id,
        doc_count=doc_count,
        avg_query_len=0.0,
        total_searches=0,
        last_updated=datetime.now(UTC),
    )


async def get_adaptive_k(
    session: AsyncSession,
    redis_client: aioredis.Redis,
    tenant_id: UUID,
    query: str,
    config: AdaptiveKConfig,
    search_config: SearchConfig,
) -> AdaptiveKResult | None:
    """Load tenant stats (doc_count via 1-hour Redis cache) and compute adaptive K.

    Returns ``None`` when the caller should fall back to the static K from
    ``SearchConfig`` (new tenant without a stats row, or any load failure).
    Never raises — adaptive K must not break the search path.
    """
    try:
        from app.db.queries import tenant_stats as tenant_stats_queries

        cache_key = _DOC_COUNT_CACHE_KEY.format(tenant_id=tenant_id)
        doc_count: int | None = None

        cached = await redis_client.get(cache_key)
        if cached is not None:
            doc_count = int(cached)
            stats = _stats_from_doc_count(tenant_id, doc_count)
        else:
            stats = await tenant_stats_queries.get(session, tenant_id)
            if stats is not None:
                await redis_client.set(
                    cache_key, stats.doc_count, ex=DOC_COUNT_CACHE_TTL_SECONDS
                )

        if stats is None:
            # New tenant: no stats row yet — static K fallback (PR-10 constraint).
            return None

        k_lexical, k_vector = compute_adaptive_k(
            stats, query, config, default_k=search_config.k_lexical
        )
        bucket = classify_bucket(stats.doc_count, config)
        return AdaptiveKResult(k_lexical=k_lexical, k_vector=k_vector, bucket=bucket)
    except Exception as exc:  # noqa: BLE001 — fallback must never break search
        logger.warning(
            "Adaptive K failed, falling back to static K",
            extra={"tenant_id": str(tenant_id), "error": str(exc)},
        )
        return None


__all__ = [
    "MIN_K",
    "MAX_K",
    "AdaptiveKResult",
    "classify_bucket",
    "compute_adaptive_k",
    "get_adaptive_k",
]
