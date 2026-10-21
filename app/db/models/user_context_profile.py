"""User context profile model (ARCHITECT §10.6, PR-05).

Stores context-specific profile vectors for personalized search. Each user can
have multiple profiles: one per context key (session, time-of-day, device).
Composite PK on (user_id, context_key) supports fast lookups and cascades
on user deletion.
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class UserContextProfile(Base):
    """Context-specific user profile vector for personalized search."""

    __tablename__ = "user_context_profiles"

    # Composite primary key (user_id, context_key)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    context_key: Mapped[str] = mapped_column(Text, primary_key=True)

    # Profile vector: EMA aggregation of clicked document embeddings
    profile_vector: Mapped[list[float] | None] = mapped_column(
        VECTOR(1024),
        nullable=True,  # NULL for cold-start or GDPR reset
    )

    # EMA metadata: adaptive alpha selection and session expiry
    click_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    profile_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index(
            "ix_user_context_profiles_session_expiry",
            "updated_at",
            postgresql_where=text("context_key LIKE 'session:%'"),
        ),
    )
