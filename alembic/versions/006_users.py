"""users table with profile_vector for personalization (ARCHITECT §10.1, PR-01).

users stores user profiles for personalized search: profile_vector is an EMA
aggregation of clicked document embeddings (1024-dim). Adaptive alpha: 0.5 for
users < 10 clicks, 0.05 for established users. GDPR compliance: profile can be
reset (null vector, zero counters) while preserving audit record.

Indexes: (tenant_id, id) for tenant-scoped lookups. Updated_at trigger mirrors
documents table for consistency.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import VECTOR

from alembic import op

revision = "006_users"
down_revision = "005_eval_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Install vector extension (required for VECTOR column type)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # users table: profile-based personalization
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_vector", VECTOR(1024), nullable=True),  # NULL for cold-start, GDPR reset
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Index("ix_users_tenant_id_id", "tenant_id", "id"),
    )

    # Updated_at trigger (reusing shared trigger function from migration 001)
    op.execute(
        """
        CREATE TRIGGER trg_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    # Drop trigger and table (extension is dropped if no longer used)
    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON users")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")