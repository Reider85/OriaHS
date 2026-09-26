"""Pydantic models for cross-encoder reranking (C-01)."""

from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class RerankCandidate(BaseModel):
    """Candidate document for reranking (ARCHITECT §7.3)."""

    model_config = ConfigDict(extra="forbid")

    doc_id: UUID
    text: str  # first 512 tokens of title + content
    score: float  # pre-fusion score (RRF or weighted)


class RerankResult(BaseModel):
    """Result of reranking a candidate document (ARCHITECT §7.3)."""

    model_config = ConfigDict(extra="forbid")

    doc_id: UUID
    score: float  # post-rerank score ∈ [0, 1] (sigmoid-normalized)