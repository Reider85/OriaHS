"""user_context_profiles table for contextual personalization (ARCHITECT §10.6, PR-05).

Stores context-specific profile vectors (session, time-of-day, device) for
personalized search. Each user can have multiple profiles: one per context
key (e.g., "session:abc123", "morning", "desktop"). EMA updates with adaptive
alpha: 0.5 for session (fast learning), 0.1 for time-of-day/device (slow).
Session profiles expire after TTL (default 24h); GDPR cascade deletes all.

Indexes: composite PK (user_id, context_key), partial index for session expiry.
Trigger trg_user_context_profiles_updated_at mirrors users table for consistency.

Note: Migration 009 taken by PR-05; PR-10 will use 010 for tenant_search_stats.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
try:
    from pgvector.sqlalchemy import VECTOR
except ImportError:
    from sqlalchemy.dialects.postgresql import VECTOR

from alembic import op

revision = "009_user_context_profiles"
down_revision = "008_ab_experiments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # user_context_profiles table: context-specific personalization profiles
    op.create_table(
        "user_context_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_key", sa.Text(), nullable=False),
        sa.Column("profile_vector", VECTOR(1024), nullable=True),  # NULL for cold-start, GDPR reset
        sa.Column("click_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_context_profiles_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "context_key", name="pk_user_context_profiles"),
    )

    # Partial index for session expiry cron (WHERE context_key LIKE 'session:%')
    op.create_index(
        "ix_user_context_profiles_session_expiry",
        "user_context_profiles",
        ["updated_at"],
        postgresql_where=sa.text("context_key LIKE 'session:%'"),
    )

    # Updated_at trigger (reusing shared trigger function from migration 001)
    op.execute(
        """
        CREATE TRIGGER trg_user_context_profiles_updated_at
        BEFORE UPDATE ON user_context_profiles
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    # Drop trigger and table
    op.execute("DROP TRIGGER IF EXISTS trg_user_context_profiles_updated_at ON user_context_profiles")
    op.drop_index("ix_user_context_profiles_session_expiry", table_name="user_context_profiles")
    op.drop_table("user_context_profiles")