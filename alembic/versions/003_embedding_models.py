"""embedding_models table + seed (ARCHITECT §3.1, §5.1, P-03).

The partial unique index enforces a single active default model
(TRIZ-gate ROADMAP §3.3 — ``is_active`` enables zero-downtime migrations).
"""

import sqlalchemy as sa

from alembic import op

revision = "003_embedding_models"
down_revision = "002_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_models",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "embedding_models_one_default_idx",
        "embedding_models",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_active = true"),
    )

    op.bulk_insert(
        sa.table(
            "embedding_models",
            sa.column("name", sa.Text()),
            sa.column("dimension", sa.Integer()),
            sa.column("description", sa.Text()),
            sa.column("is_default", sa.Boolean()),
            sa.column("is_active", sa.Boolean()),
        ),
        [
            {
                "name": "bge-m3-v1",
                "dimension": 1024,
                "description": "BAAI/bge-m3 multilingual dense embeddings",
                "is_default": True,
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("embedding_models_one_default_idx", table_name="embedding_models")
    op.drop_table("embedding_models")
