"""Shared request/response models for the API (ARCHITECT §14.1, §14.2).

P-07 adds the ``/index`` write-path models; P-11 adds the ``/search``
models here. Status values are frozen here so the fast-path no-op is
observable to clients instead of being a silent 201.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.search.filters import SearchFilters


class IndexRequest(BaseModel):
    """Body of ``POST /index`` (ARCHITECT §14.2)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    external_ref: str
    title: str
    content: str
    language: str | None = None  # None → langdetect on title + content
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class IndexResponse(BaseModel):
    """Response of ``POST /index`` (ARCHITECT §14.2, MVP subset).

    ``status`` is ``"queued"`` when a new outbox task was created and
    ``"no_change"`` when the fast-path re-index of identical content was
    skipped. ``"indexed"/"throttled"`` ship in the Critical phase.
    ``wait_for_index_token`` is used for polling index status in Critical.
    """

    doc_id: UUID
    status: Literal["queued", "no_change"]
    indexed_at: datetime
    wait_for_index_token: str | None = None


# ---------------------------------------------------------------------------
# P-11: Search read-path models (ARCHITECT §14.1, MVP subset)
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """Body of ``POST /search`` (ARCHITECT §14.1, Critical subset).

    ``fusion`` selects the fusion strategy — ``"rrf"`` (default, ARCHITECT
    §7.1) or ``"weighted"`` (ARCHITECT §7.2, C-02). ``fusion_alpha`` is the
    lexical weight for weighted fusion and is ignored for RRF.

    ``rerank`` opts into cross-encoder reranking (C-01). It is honoured only
    when the reranker is injected, the global ``rerank_enabled`` flag is on,
    and the circuit breaker is closed (C-03); otherwise the request is served
    fusion-only and the per-hit ``debug`` reports ``rerank_degraded=True``.

    ``facets`` requests facet aggregation on the top-K results for specified
    fields (e.g., ``["tags", "attributes.category"]``). If ``None``, no facets
    are computed to save resources.

    Fields deferred to Production-Ready are omitted: ``personalize``,
    ``diversify``, ``experiment_id``, ``context``.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=2048)
    tenant_id: UUID
    user_id: UUID | None = None  # accepted but ignored on MVP
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(20, ge=1, le=100)
    fusion: Literal["rrf", "weighted"] = "rrf"  # RRF + weighted in Critical
    rerank: bool = False  # cross-encoder rerank in Critical
    fusion_alpha: float = Field(0.5, ge=0.0, le=1.0)  # for weighted fusion
    explain: bool = False
    timeout_ms: int = Field(200, ge=50, le=2000)
    facets: list[str] | None = None  # list of fields to facet: ["tags", "attributes.category"]
    facet_top_n: int = Field(20, ge=1, le=100)  # max buckets per facet field


class SearchHit(BaseModel):
    """One fused search result returned to the client (ARCHITECT §14.1).

    ``score`` is the final ranking score: the cross-encoder score when rerank
    was applied to this document, otherwise the fusion score. ``debug`` is
    populated only when ``explain=True`` and carries:

    - ``fusion_strategy`` — ``"rrf"`` or ``"weighted"`` (C-05)
    - ``fusion_alpha`` — lexical weight, weighted fusion only
    - ``rrf_score`` / ``weighted_score`` — pre-rerank fusion score
    - ``lexical_rank`` / ``vector_rank`` — 1-based position in each channel
    - ``lexical_score`` / ``vector_score`` — raw per-channel scores
    - ``rerank_applied`` — cross-encoder ran for this request (C-05)
    - ``rerank_degraded`` — rerank skipped because the circuit breaker was open (C-05)
    - ``rerank_score`` — sigmoid-normalized cross-encoder score, ``None`` if not reranked
    """

    doc_id: UUID
    score: float
    title: str
    snippet: str
    attributes: dict[str, Any]
    debug: dict[str, Any] | None = None


class FacetBucket(BaseModel):
    """A single bucket inside a facet aggregation (ARCHITECT §14.1).

    Facets are empty on MVP — populated in the Critical phase.
    """

    value: str
    count: int


class SearchResponse(BaseModel):
    """Response of ``POST /search`` (ARCHITECT §14.1, Critical subset).

    ``facets`` stays empty until C-06. ``degraded`` is ``True`` when a search
    *channel* failed and results come from the surviving channel only.

    ``degraded`` and ``rerank_degraded`` carry deliberately distinct semantics
    (C-05): ``degraded`` means the vector channel was lost (lex-only fallback,
    C-09), while ``rerank_degraded`` means rerank was requested but skipped
    because the circuit breaker was open. A request served from fusion-only
    results because of the breaker returns ``200`` with ``degraded=False`` and
    ``rerank_degraded=True`` on the per-hit ``debug``.

    ``partial`` is ``True`` when the deadline cut the pipeline short and the
    response holds only what completed in time (C-09).
    """

    hits: list[SearchHit]
    facets: dict[str, list[FacetBucket]] = Field(default_factory=dict)
    total_lexical: int
    total_vector: int
    latency_ms: int
    degraded: bool = False
    partial: bool = False
