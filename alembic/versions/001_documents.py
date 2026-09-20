"""documents table + empty partitioned search_events (ARCHITECT §3.1, P-01).

Also installs the pg_trgm / pgcrypto extensions and the shared
``set_updated_at()`` trigger function (reused by search_outbox in P-02).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001_documents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "embedding_rev",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("content_hash", sa.Text(), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "external_ref",
            name="documents_tenant_id_external_ref_key",
        ),
    )

    op.create_index(
        "documents_tsv_idx",
        "documents",
        ["tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "documents_trgm_idx",
        "documents",
        ["title", "content"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops", "content": "gin_trgm_ops"},
    )
    op.create_index(
        "documents_tenant_lang_idx",
        "documents",
        ["tenant_id", "language"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "documents_attrs_gin_idx",
        "documents",
        ["attributes"],
        postgresql_using="gin",
        postgresql_ops={"attributes": "jsonb_path_ops"},
    )
    op.create_index(
        "documents_tags_gin_idx",
        "documents",
        ["tags"],
        postgresql_using="gin",
    )

    op.execute(
        """
        CREATE TRIGGER trg_documents_updated_at
        BEFORE UPDATE ON documents
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    # TRIZ-gate (ROADMAP §3.3): lay the partitioned structure now; it is
    # populated only in Production-Ready. ``user_id`` FK to ``users`` is
    # omitted because ``users`` does not exist in MVP (ARCHITECT §3.1).
    # NOTE: PK must include the partition column (PG requirement).
    op.execute(
        """
        CREATE TABLE search_events (
            id              BIGSERIAL,
            user_id         UUID,
            tenant_id       UUID NOT NULL,
            query_text      TEXT NOT NULL,
            query_lang      TEXT,
            top_doc_ids     UUID[] NOT NULL DEFAULT '{}',
            clicked_doc_id  UUID,
            position        INT,
            fusion_strategy TEXT,
            experiment_id   TEXT,
            latency_ms      INT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        """
    )
    op.execute("CREATE TABLE search_events_default PARTITION OF search_events DEFAULT;")
    op.execute(
        "CREATE INDEX search_events_user_time_idx "
        "ON search_events (user_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX search_events_exp_idx ON search_events (experiment_id, created_at DESC);"
    )


def downgrade() -> None:
    op.drop_index("search_events_exp_idx", table_name="search_events")
    op.drop_index("search_events_user_time_idx", table_name="search_events")
    op.drop_table("search_events")

    op.execute("DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents")
    op.drop_index("documents_tags_gin_idx", table_name="documents")
    op.drop_index("documents_attrs_gin_idx", table_name="documents")
    op.drop_index("documents_tenant_lang_idx", table_name="documents")
    op.drop_index("documents_trgm_idx", table_name="documents")
    op.drop_index("documents_tsv_idx", table_name="documents")
    op.drop_table("documents")

    # Extensions and the shared trigger function are kept (spec: drop only tables).
