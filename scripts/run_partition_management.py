"""Daily partition management for search_events (PR-02).

Creates daily partitions ahead of time, archives old partitions to S3 Parquet,
and drops them. Designed for daily cron execution.

Usage:
    python scripts/run_partition_management.py

Exit codes:
    0 - Success
    1 - Partition management failed
    2 - S3 archive failed
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta

from app.config import settings
from app.db.queries.search_events import SearchEventQueries

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run partition management: create ahead, archive old, drop old."""
    try:
        logger.info("Starting partition management")

        # Get partition management config
        search_events_config = settings.search_events
        queries = SearchEventQueries(search_events_config)

        # Step 1: Ensure partitions exist for the next N days
        logger.info(f"Ensuring partitions for next {search_events_config.partition_ahead_days} days")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        for i in range(search_events_config.partition_ahead_days):
            partition_date = today + timedelta(days=i)
            await queries.create_daily_partition(session, partition_date)
            logger.debug(f"Ensured partition for {partition_date.strftime('%Y-%m-%d')}")

        # Step 2: Find and archive partitions older than retention days
        logger.info(f"Archiving partitions older than {search_events_config.retention_days} days")
        cutoff_date = today - timedelta(days=search_events_config.retention_days)

        # List all partitions
        partitions = await queries.list_partitions(session)
        archived_count = 0
        dropped_count = 0

        for partition_name, upper_bound in partitions:
            if upper_bound < cutoff_date:
                logger.info(f"Archiving partition {partition_name} (bound: {upper_bound.strftime('%Y-%m-%d')})")

                try:
                    # Archive to S3
                    s3_key = await queries.archive_partition_to_s3(
                        session, partition_name, search_events_config.s3_bucket
                    )
                    logger.info(f"Archived {partition_name} to s3://{search_events_config.s3_bucket}/{s3_key}")
                    archived_count += 1

                    # Drop the partition
                    await queries.drop_partition(session, partition_name)
                    logger.info(f"Dropped partition {partition_name}")
                    dropped_count += 1

                except Exception as e:
                    logger.error(f"Failed to archive/drop partition {partition_name}: {e}")
                    # Continue with next partition instead of failing the whole process
                    continue

        # Step 3: Update metrics
        logger.info(f"Partition management complete: {archived_count} archived, {dropped_count} dropped")

        # Exit successfully
        sys.exit(0)

    except Exception as e:
        logger.error(f"Partition management failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Get session from app context
    from app.db.session import async_session_factory

    async def run_with_session():
        async with async_session_factory() as session:
            await main()

    # Run the main function
    asyncio.run(run_with_session())
