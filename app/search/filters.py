"""Search filter models (ARCHITECT §14.1, §8.3).

Shared between lexical (P-09), vector (P-10) and orchestrator (P-11)
channels. Pre-filtered at the API layer so each search backend receives
an already-validated filter set.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SearchFilters(BaseModel):
    """Query-time filters applied before ranking (ARCHITECT §14.1)."""

    model_config = ConfigDict(extra="forbid")

    language: list[str] | None = None
    tags_any: list[str] | None = None
    attributes: dict[str, Any] | None = None
    created_after: datetime | None = None
