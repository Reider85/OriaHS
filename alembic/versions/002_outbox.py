"""search_outbox table + indexes (ARCHITECT §3.1, P-02).

Status CHECK includes the TRIZ-gate ``dead`` state (ROADMAP §3.3) so the
reconciler always has a safety net for permanently failing records.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "002_outbox"
down_revision = "001_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("op IN ('upsert','delete')", name="op_valid"),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','done','failed','dead')",
            name="status_valid",
        ),
    )

    op.execute(
        """
        CREATE TRIGGER trg_search_outbox_updated_at
        BEFORE UPDATE ON search_outbox
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    op.create_index(
        "search_outbox_pending_idx",
        "search_outbox",
        ["next_retry_at"],
        postgresql_where=sa.text("status IN ('pending','failed')"),
    )
    op.create_index(
        "search_outbox_doc_idx",
        "search_outbox",
        ["document_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("search_outbox_doc_idx", table_name="search_outbox")
    op.drop_index("search_outbox_pending_idx", table_name="search_outbox")
    op.execute("DROP TRIGGER IF EXISTS trg_search_outbox_updated_at ON search_outbox")
    op.drop_table("search_outbox")
