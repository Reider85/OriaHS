"""SQLAlchemy 2.x model for the ``users`` table (ARCHITECT §10.1, PR-01)."""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class User(Base):
    """User profile for personalized search (EMA aggregation of clicked embeddings)."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_tenant_id_id", "tenant_id", "id"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    profile_vector: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)
    profile_version: Mapped[int] = mapped_column(server_default="0", nullable=False)
    click_count: Mapped[int] = mapped_column(server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )
