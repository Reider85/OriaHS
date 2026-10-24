"""search_events variant column DEFAULT 'control' and backfill NULLs (PR-08).

Sets DEFAULT 'control' on variant column for new inserts and backfills existing
NULL values to 'control'. Ensures all search_events rows have a variant value
with 'control' as the default when no experiment is active.

Downgrade removes the DEFAULT (NULL values are not restored - intentional).
"""

import sqlalchemy as sa
from alembic import op

revision = "011_search_events_variant_default"
down_revision = "010_model_migrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Set DEFAULT 'control' on variant column
    # This default is inherited by all partitions automatically
    op.alter_column(
        "search_events",
        "variant",
        existing_type=sa.Text(),
        server_default="control",
    )
    
    # Backfill existing NULL values to 'control'
    # UPDATE on partitioned parent propagates to all partitions
    op.execute(
        "UPDATE search_events SET variant = 'control' WHERE variant IS NULL"
    )


def downgrade() -> None:
    # Remove DEFAULT - NULL values are not restored (intentional)
    op.alter_column(
        "search_events",
        "variant",
        existing_type=sa.Text(),
        server_default=None,
    )