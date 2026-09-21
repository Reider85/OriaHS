"""Shared request/response models for the API (ARCHITECT §14.1, §14.2).

P-07 adds the ``/index`` write-path models; P-11 will add the ``/search``
models here. Status values are frozen here so the fast-path no-op is
observable to clients instead of being a silent 201.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    """

    doc_id: UUID
    status: Literal["queued", "no_change"]
    indexed_at: datetime
    wait_for_index_token: str | None = None
