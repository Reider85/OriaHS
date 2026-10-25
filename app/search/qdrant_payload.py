"""Canonical Qdrant point payload model (ARCHITECT §3.2, §17.1; P-04).

Single source of truth shared by indexing (P-07), vector search (P-10) and
the reconciler (P-13). ``extra='forbid'`` prevents silent schema drift.

Schema Drift Process Gate:
============================
When modifying QdrantPayload or Document models, follow this process:

1. Update field mapping in tests/contract/test_schema_drift.py constants
2. Update this documentation table
3. Ensure all contract tests pass (`pytest tests/contract/`)
4. Release is blocked if tests fail — process gate (ROADMAP §5.2.14)

Field Mapping (QdrantPayload ↔ Document columns):
================================================
| QdrantPayload field | Document column     | Notes                          |
|---------------------|---------------------|--------------------------------|
| doc_id              | id                  | Primary key                    |
| tenant_id           | tenant_id           | Tenant identifier              |
| language            | language            | Document language              |
| tags                | tags                | ARRAY(Text) → list[str]        |
| attributes          | attributes          | JSONB → dict[str, Any]         |
| model_name          | embedding_model     | Embedding model name           |
| model_rev           | embedding_rev       | Model revision                 |
| created_at          | created_at          | Timestamp with UTC timezone    |
| content_hash        | content_hash        | Content fingerprint            |
| chunk_idx           | -                   | Qdrant-side chunk index (PR-13)|
| section_type        | -                   | Qdrant-side section type (PR-13)|

Excluded from payload:
- external_ref, title, content (not stored in vector index)
- updated_at, deleted_at (internal state)
- tsv (PostgreSQL text search vector)

Process: When adding a field to QdrantPayload, update the mapping table and
contract constants. When modifying Document columns, update mapping and
ensure backward compatibility.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Document


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
    chunk_idx: int = 0
    section_type: str | None = None

    @classmethod
    def from_document(cls, document: Document, embedding: list[float], chunk_idx: int = 0, section_type: str | None = None) -> "QdrantPayload":
        """Create QdrantPayload from Document model.

        Args:
            document: Document model instance
            embedding: Embedding vector
            chunk_idx: Chunk index for asymmetric indexing
            section_type: Section type for asymmetric indexing

        Returns:
            QdrantPayload instance

        Raises:
            ValueError: If document is deleted or embedding dimension is wrong
        """
        # Check if document is deleted
        if document.deleted_at is not None:
            raise ValueError("Cannot create QdrantPayload from deleted document")

        # Validate embedding dimension (should be 1024 for bge-m3)
        if len(embedding) != 1024:
            raise ValueError(f"Expected embedding dimension 1024, got {len(embedding)}")

        return cls(
            doc_id=document.id,
            tenant_id=document.tenant_id,
            language=document.language,
            tags=document.tags,
            attributes=document.attributes,
            model_name=document.embedding_model,
            model_rev=document.embedding_rev,
            created_at=document.created_at,
            content_hash=document.content_hash,
            chunk_idx=chunk_idx,
            section_type=section_type
        )

    @classmethod
    def from_qdrant_point(cls, point: Any) -> "QdrantPayload":
        """Create QdrantPayload from Qdrant point.

        Args:
            point: Qdrant point structure

        Returns:
            QdrantPayload instance
        """
        return cls(**point.payload)
