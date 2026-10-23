"""model_migrations table for embedding model migration framework (PR-07).

Tracks migration lifecycle: init → canary_passed/canary_failed → backfilled → 
promoted → rolled_back | cleaned. Stores migration metadata including 
old/new model names, collections, HF model IDs, canary statistics, 
S3 snapshot keys, and backfill progress. Enables blue-green deployment, 
dual-write, rollback, and cleanup with feature-flagged search switching.

Dual-write: reconciler writes to both old and new collections with respective 
model vectors until cleanup. Search switches to new collection/model via 
dynamic lookup (DB-driven + env override). Canary similarity thresholds: 
min=0.6, max=0.95 (avg). Snapshots stored in S3 for rollback safety.

Index: status for fast lookup, updated_at trigger for consistency.
Downgrade: cascade delete migration records (embedding models preserved).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
try:
    from pgvector.sqlalchemy import VECTOR
except ImportError:
    from sqlalchemy.dialects.postgresql import VECTOR

from alembic import op

revision = "010_model_migrations"
down_revision = "009_user_context_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # model_migrations table: migration lifecycle and metadata
    op.create_table(
        "model_migrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("old_model_name", sa.Text(), nullable=False),
        sa.Column("new_model_name", sa.Text(), nullable=False),
        sa.Column("old_collection", sa.Text(), nullable=False),
        sa.Column("new_collection", sa.Text(), nullable=False),
        sa.Column("new_hf_model", sa.Text(), nullable=False),  # HF model ID for new model loading
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("init")),
        sa.Column("canary_avg_similarity", sa.Float(), nullable=True),
        sa.Column("canary_min_similarity", sa.Float(), nullable=True),
        sa.Column("canary_max_similarity", sa.Float(), nullable=True),
        sa.Column("canary_passed", sa.Boolean(), nullable=True),
        sa.Column("snapshot_s3_key", sa.Text(), nullable=True),
        sa.Column("backfill_processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("backfill_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["old_model_name"],
            ["embedding_models.name"],
            name="fk_model_migrations_old_model_name",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["new_model_name"],
            ["embedding_models.name"],
            name="fk_model_migrations_new_model_name",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_migrations"),
    )

    # Index for fast status-based lookup (active, promoted migrations)
    op.create_index(
        "ix_model_migrations_status",
        "model_migrations",
        ["status"],
    )

    # Updated_at trigger (reusing shared trigger function from migration 001)
    op.execute(
        """
        CREATE TRIGGER trg_model_migrations_updated_at
        BEFORE UPDATE ON model_migrations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    # Drop trigger and table
    op.execute("DROP TRIGGER IF EXISTS trg_model_migrations_updated_at ON model_migrations")
    op.drop_index("ix_model_migrations_status", table_name="model_migrations")
    op.drop_table("model_migrations")