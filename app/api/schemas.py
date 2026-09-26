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
    """Body of ``POST /search`` (ARCHITECT §14.1, MVP subset).

    Fields deferred to Critical/Production-Ready are omitted:
    ``personalize``, ``diversify``, ``experiment_id``, ``context``.
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


class SearchHit(BaseModel):
    """One fused search result returned to the client (ARCHITECT §14.1)."""

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
    """Response of ``POST /search`` (ARCHITECT §14.1, MVP subset).

    ``facets`` is always empty on MVP. ``degraded`` is ``True`` when one
    channel failed and results come from the surviving channel only.
    """

    hits: list[SearchHit]
    facets: dict[str, list[FacetBucket]] = Field(default_factory=dict)
    total_lexical: int
    total_vector: int
    latency_ms: int
    degraded: bool = False
