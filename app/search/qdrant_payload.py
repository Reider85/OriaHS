"""Canonical Qdrant point payload model (ARCHITECT §3.2, §17.1; P-04).

Single source of truth shared by indexing (P-07), vector search (P-10) and
the reconciler (P-13). ``extra='forbid'`` prevents silent schema drift.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QdrantPayload(BaseModel):
    """Payload of one Qdrant point in the ``documents`` collection."""

    model_config = ConfigDict(extra="forbid")

    doc_id: UUID
    tenant_id: UUID
    language: str
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    model_name: str
    model_rev: int
    created_at: datetime
    content_hash: str
