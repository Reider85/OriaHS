"""eval_datasets and eval_results tables (ARCHITECT §11.5, C-07).

eval_datasets tracks eval dataset metadata (name, version, path, query_count).
eval_results stores strategy performance metrics (Recall@10, nDCG@10, MRR) with baseline comparison.

The nightly eval job loads a dataset, registers it, runs all fusion strategies,
computes metrics, saves results, and detects regressions against the previous run.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision = "005_eval_datasets"
down_revision = "004_dead_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # eval_datasets: metadata about evaluation datasets
    op.create_table(
        "eval_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", "version", name="uq_eval_datasets_name_version"),
    )

    # eval_results: performance metrics for each strategy run
    op.create_table(
        "eval_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),  # 'rrf', 'weighted', 'weighted+rerank'
        sa.Column("recall_at_10", sa.Float(), nullable=False),
        sa.Column("ndcg_at_10", sa.Float(), nullable=False),
        sa.Column("mrr", sa.Float(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("git_sha", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name="fk_eval_results_dataset_id",
            ondelete="CASCADE",
        ),
        sa.Index("ix_eval_results_dataset_strategy_run_at", "dataset_id", "strategy", "run_at"),
    )


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_datasets")