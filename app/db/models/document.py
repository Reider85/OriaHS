"""SQLAlchemy 2.x model for the ``documents`` table (ARCHITECT §3.1, P-01)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Computed, DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class Document(Base):
    """Source of truth for hybrid search (one row per indexed document)."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "external_ref",
            name="documents_tenant_id_external_ref_key",
        ),
        Index("documents_tsv_idx", "tsv", postgresql_using="gin"),
        Index(
            "documents_trgm_idx",
            "title",
            "content",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops", "content": "gin_trgm_ops"},
        ),
        Index(
            "documents_tenant_lang_idx",
            "tenant_id",
            "language",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "documents_attrs_gin_idx",
            "attributes",
            postgresql_using="gin",
            postgresql_ops={"attributes": "jsonb_path_ops"},
        ),
        Index("documents_tags_gin_idx", "tags", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID]
    external_ref: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'"),
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_rev: Mapped[int] = mapped_column(server_default=text("1"))
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # GENERATED ALWAYS AS (...) STORED — computed server-side, read-only for ORM.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))",
            persisted=True,
        ),
    )
