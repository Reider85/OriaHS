"""search_events daily partitions for efficient archiving (PR-02).

Creates 30 daily partitions for search_events table (PARTITION BY RANGE created_at).
Each partition is named search_events_YYYYMMDD and indexes are copied from parent table.
This enables fast archival of old partitions (DROP PARTITION) and efficient queries.

Partition boundaries: FROM (date) TO (date+1day). Default partition search_events_default
exists from migration 001 and receives dates outside the 30-day window.

Retention: partitions older than retention_days (90 days) are archived to S3 Parquet
and dropped via DROP PARTITION (O(1) operation, no VACUUM needed).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "007_search_events_partitions"
down_revision = "006_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create 30 daily partitions starting from current date
    # Each partition covers one day: FROM (date) TO (date+1day)
    current_date = sa.text("CURRENT_DATE")
    
    # Generate partition names and bounds for 30 days
    partition_sql = []
    for i in range(30):
        partition_date = current_date + sa.text(f"INTERVAL '{i} days'")
        next_date = partition_date + sa.text("INTERVAL '1 day'")
        
        partition_name = sa.text(f"search_events_{sa.func.to_char(partition_date, 'YYYYMMDD')}")
        partition_bound_from = partition_date
        partition_bound_to = next_date
        
        partition_sql.append(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name} 
            PARTITION OF search_events 
            FOR VALUES FROM ({partition_bound_from}) TO ({partition_bound_to})
            """
        )
    
    # Execute all partition creation statements
    op.execute(sa.text(";\n".join(partition_sql)))
    
    # Create indexes on each partition (indexes don't inherit from parent)
    # These indexes match the parent table: search_events_user_time_idx, search_events_exp_idx
    index_sql = []
    for i in range(30):
        partition_date = current_date + sa.text(f"INTERVAL '{i} days'")
        partition_name = sa.text(f"search_events_{sa.func.to_char(partition_date, 'YYYYMMDD')}")
        
        # User + time index (for user history queries)
        index_sql.append(
            f"""
            CREATE INDEX IF NOT EXISTS ix_{partition_name}_user_time_idx 
            ON {partition_name} (user_id, created_at DESC)
            """
        )
        
        # Experiment index (for A/B testing analysis)
        index_sql.append(
            f"""
            CREATE INDEX IF NOT EXISTS ix_{partition_name}_exp_idx 
            ON {partition_name} (experiment_id, created_at DESC)
            """
        )
    
    # Execute all index creation statements
    op.execute(sa.text(";\n".join(index_sql)))


def downgrade() -> None:
    # Drop all daily partitions (keep search_events_default from migration 001)
    # Drop indexes first, then tables
    current_date = sa.text("CURRENT_DATE")
    
    # Generate partition names for 30 days
    drop_sql = []
    for i in range(30):
        partition_date = current_date + sa.text(f"INTERVAL '{i} days'")
        partition_name = sa.text(f"search_events_{sa.func.to_char(partition_date, 'YYYYMMDD')}")
        
        # Drop indexes
        drop_sql.append(
            f"""
            DROP INDEX IF EXISTS ix_{partition_name}_user_time_idx;
            DROP INDEX IF NOT EXISTS ix_{partition_name}_exp_idx;
            DROP TABLE IF EXISTS {partition_name};
            """
        )
    
    # Execute all drop statements
    op.execute(sa.text(";\n".join(drop_sql)))