"""tenant_search_stats table for adaptive K (ARCHITECT §3.1, §6.4, PR-10).

One row per tenant: doc_count (updated daily by scripts/update_tenant_stats.py),
avg_query_len (running average, updated atomically on every search) and
total_searches. The search orchestrator reads this table (doc_count via a
1-hour Redis cache) to adapt K_lexical/K_vector per tenant.

Numbering note: PROD-PROMPTS §0.4 sketches 008/010 for this table, but those
numbers were taken by PR-03 (008_ab_experiments) and PR-07 (010_model_migrations)
— PR-10 takes 012.

No FK to a tenants table: documents/users use bare tenant_id UUIDs without
foreign keys (tenants are provisioned out-of-band).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "012_tenant_search_stats"
down_revision = "011_search_events_variant_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_search_stats",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("doc_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("avg_query_len", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_searches", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("tenant_search_stats")
