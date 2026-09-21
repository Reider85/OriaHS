"""search_outbox_dead_digest table (ARCHITECT §4.4, P-14).

Hourly digest of ``dead`` outbox rows grouped by
``(tenant_id, model_name, error_type)`` — the "health map" that turns
otherwise unreadable 10k+ dead letters into ~10–100 diagnostic rows
(TRIZ-gate ROADMAP §3.3; ARCHITECT §4.7 principle 22).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "004_dead_digest"
down_revision = "003_embedding_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_outbox_dead_digest",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sample_doc_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "sample_errors",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "model_name",
            "error_type",
            name="search_outbox_dead_digest_tenant_model_error_key",
        ),
    )
    op.create_index(
        "search_outbox_dead_digest_tenant_idx",
        "search_outbox_dead_digest",
        ["tenant_id"],
    )
    op.create_index(
        "search_outbox_dead_digest_last_seen_idx",
        "search_outbox_dead_digest",
        ["last_seen_at"],
        postgresql_ops={"last_seen_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index(
        "search_outbox_dead_digest_last_seen_idx",
        table_name="search_outbox_dead_digest",
    )
    op.drop_index(
        "search_outbox_dead_digest_tenant_idx",
        table_name="search_outbox_dead_digest",
    )
    op.drop_table("search_outbox_dead_digest")
