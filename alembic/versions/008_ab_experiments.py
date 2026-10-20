"""ab_experiments table + search_events.variant for the A/B framework (ARCHITECT §11.3, PR-03).

ab_experiments stores experiment definitions: variants (JSONB mapping variant
name -> parameter overlay applied to SearchRequest), weights (traffic split),
status lifecycle (active/stopped) and the winning variant on stop.

search_events gains a ``variant`` TEXT column so per-variant metrics (CTR, MRR,
no-click rate, latency p95) can be aggregated daily per experiment (ARCHITECT
§11.4). The column is added on the partitioned parent — PostgreSQL propagates
it to every daily partition automatically. Shadow traffic (PR-08) reuses the
same column.

Numbering note: PROD-PROMPTS §0.4 sketches 008_tenant_search_stats for PR-10;
PR-03 executes first and its Goal assigns 008_ab_experiments, so PR-10 takes
009 in a later prompt.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "008_ab_experiments"
down_revision = "007_search_events_partitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ab_experiments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("experiment_id", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "variants",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "weights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("winning_variant", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'stopped')",
            name="ck_ab_experiments_status",
        ),
        sa.Index("ix_ab_experiments_status_idx", "status"),
    )

    # Per-variant attribution for daily experiment metrics (ARCHITECT §11.3).
    # Propagates to all existing partitions of the partitioned parent.
    op.execute("ALTER TABLE search_events ADD COLUMN IF NOT EXISTS variant TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_events_variant_idx "
        "ON search_events (variant, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_search_events_variant_idx")
    op.execute("ALTER TABLE search_events DROP COLUMN IF EXISTS variant")
    op.drop_table("ab_experiments")
