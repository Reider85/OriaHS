"""Shared request/response models for the API (ARCHITECT §14.1, §14.2).

P-07 adds the ``/index`` write-path models; P-11 adds the ``/search``
models here. Status values are frozen here so the fast-path no-op is
observable to clients instead of being a silent 201.
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.search.filters import SearchFilters


class SearchContext(BaseModel):
    """Search context for personalization (ARCHITECT §10.6)."""

    device: Literal["desktop", "mobile", "tablet"] | None = None
    session_id: str | None = None
    time_of_day: Literal["morning", "afternoon", "evening", "night"] | None = None


class PersonalizationDebug(BaseModel):
    """Debug information for personalized search (PR-01, PR-05)."""

    applied: bool
    cold_start: bool
    profile_version: int
    context_profile_key: str | None = None  # Context key used for personalization


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
    """Response of ``POST /index`` (ARCHITECT §14.2, Critical subset).

    ``status`` is ``"queued"`` when a new outbox task was created and
    ``"no_change"`` when the fast-path re-index of identical content was
    skipped. ``"throttled"`` when adaptive throttle is active (C-11).
    ``wait_for_index_token`` is used for polling index status in Critical.
    """

    doc_id: UUID
    status: Literal["queued", "no_change", "throttled"]
    indexed_at: datetime
    wait_for_index_token: str | None = None
    throttled: bool = False


class IndexStatusResponse(BaseModel):
    """Response of ``GET /index/status/{token}`` (ARCHITECT §14.3)."""

    token: str
    status: Literal["pending", "done", "dead"]


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

    Production-Ready fields added: ``personalize``, ``diversify``,
    ``experiment_id``, ``context``.
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
    personalize: bool = False  # apply user personalization (PR-01)
    diversify: bool = False  # apply MMR diversification (PR-04)
    diversify_lambda: float | None = Field(None, ge=0.0, le=1.0)  # MMR λ override (PR-04)
    experiment_id: str | None = None  # A/B experiment identifier (PR-03)
    context: SearchContext | None = None  # search context (PR-05)


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

    ``personalization`` contains debug info when personalized search is applied
    (PR-01). Present only when ``personalize=True`` and personalization was enabled.
    """

    hits: list[SearchHit]
    facets: dict[str, list[FacetBucket]] = Field(default_factory=dict)
    total_lexical: int
    total_vector: int
    latency_ms: int
    degraded: bool = False
    partial: bool = False
    variant: str | None = None  # shadow traffic variant identifier (PR-08)
    personalization: PersonalizationDebug | None = None  # PR-01 personalization debug
    fusion_strategy: str | None = None  # fusion strategy used


# ---------------------------------------------------------------------------
# PR-02: Search events models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PR-03: A/B testing models
# ---------------------------------------------------------------------------


class ExperimentCreate(BaseModel):
    """Body of ``POST /experiments`` (PR-03).

    Create a new A/B testing experiment with variants and optional weights.
    ``variants`` is either a plain list of names (each gets an empty
    parameter overlay) or a mapping name -> parameter overlay applied to
    ``SearchRequest`` (``fusion``, ``fusion_alpha``, ``rerank``,
    ``personalize``, ``diversify`` — all optional).
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(..., min_length=1)  # unique experiment identifier
    description: str  # human-readable description
    variants: list[str] | dict[str, dict[str, Any]]  # names or name -> overlay
    weights: list[float] | None = None  # optional weights for distribution
    start_date: date  # when the experiment starts

    @model_validator(mode="after")
    def _validate_variants_and_weights(self) -> "ExperimentCreate":
        names = list(self.variants) if isinstance(self.variants, list) else list(self.variants)
        if len(names) < 2:
            raise ValueError("at least two variants are required")
        if any(not name for name in names):
            raise ValueError("variant names must be non-empty")
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        if self.weights is not None:
            if len(self.weights) != len(names):
                raise ValueError("weights length must match the number of variants")
            if any(w < 0 for w in self.weights):
                raise ValueError("weights must be non-negative")
            if sum(self.weights) <= 0:
                raise ValueError("weights must sum to a positive value")
        return self


class ExperimentMetricsResponse(BaseModel):
    """Response of ``GET /experiments/{id}/metrics`` (PR-03).

    Daily per-variant metrics for an experiment.
    """

    model_config = ConfigDict(extra="forbid")

    date: date  # date for these metrics
    metrics: dict[str, dict[str, float]]  # variant → metrics dict


class StopExperimentRequest(BaseModel):
    """Body of ``POST /experiments/{id}/stop`` (PR-03).

    Stop an experiment and declare winning variant.
    """

    model_config = ConfigDict(extra="forbid")

    winning_variant: str  # winning variant name


class ClickRequest(BaseModel):
    """Body of ``POST /search/click`` (PR-02, PR-05).

    Records user click interactions for analytics and personalization.
    tenant_id is required as search_events.tenant_id is NOT NULL.
    context is used for contextual personalization updates (PR-05).
    """

    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None = None  # optional, for personalization
    tenant_id: UUID  # required, NOT NULL in search_events table
    query: str  # original query text (for analytics)
    clicked_doc_id: UUID
    position: int | None = None  # 1-based position in results, optional
    experiment_id: str | None = None  # A/B test identifier, optional
    context: SearchContext | None = None  # for contextual personalization (PR-05)


class ShadowExperimentCreate(BaseModel):
    """Body of ``POST /experiments/shadow`` (PR-08).
    
    Creates a shadow traffic experiment for parallel strategy evaluation.
    """
    
    model_config = ConfigDict(extra="forbid")
    
    experiment_id: str  # unique experiment identifier
    description: str | None = None  # optional description
    control_strategy: str = "rrf"  # production strategy name
    treatment_strategies: list[str]  # alternative strategies to evaluate


@dataclass
class VariantMetrics(BaseModel):
    """Variant metrics for shadow comparison."""
    
    count: int
    ctr: float  # click-through rate
    mrr: float  # mean reciprocal rank
    no_click_rate: float
    latency_p95: float


class ShadowComparisonResponse(BaseModel):
    """Response for ``GET /experiments/shadow/{id}/comparison`` (PR-08)."""
    
    control: VariantMetrics
    treatment: VariantMetrics
    
    model_config = ConfigDict(extra="forbid")
