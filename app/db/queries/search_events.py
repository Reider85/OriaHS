"""Query helpers for the ``search_events`` table (ARCHITECT §3.1, PR-02).

Search events partitioning and archiving: daily partitions, S3 Parquet export,
and partition management. All functions use raw SQL for consistency and performance.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import Table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass

from app.config import SearchEventsConfig
from app.db.queries.base import BaseQueries
from app.observability import metrics

logger = logging.getLogger(__name__)


class SearchEventQueries(BaseQueries):
    """Search events partitioning and archiving operations."""

    def __init__(self, config: SearchEventsConfig):
        self.config = config

    async def insert_search_event(
        self,
        session: AsyncSession,
        *,
        user_id: str | None,
        tenant_id: str,
        query_text: str,
        query_lang: str | None,
        top_doc_ids: list[str],
        fusion_strategy: str | None,
        experiment_id: str | None,
        variant: str | None = None,
        latency_ms: int | None,
    ) -> int:
        """Insert a search event into the appropriate daily partition.

        Args:
            session: Async session
            user_id: User UUID (optional, for personalization)
            tenant_id: Tenant UUID
            query_text: Search query text (may be hashed if PII enabled)
            query_lang: Query language (ISO 639-1)
            top_doc_ids: List of top-K document UUIDs
            fusion_strategy: Fusion strategy used (e.g., "rrf", "weighted")
            experiment_id: A/B test experiment ID (optional)
            variant: Assigned A/B variant name (optional, PR-03)
            latency_ms: Query latency in milliseconds

        Returns:
            Inserted event ID
        """
        # Hash query text if PII protection is enabled
        if self.config.hash_query_text:
            query_hash = hashlib.sha256(query_text.encode()).hexdigest()
            query_text = f"sha256:{query_hash}"

        # Insert into the appropriate partition (handled by PostgreSQL automatically)
        result = await session.execute(
            text(
                """
                INSERT INTO search_events (
                    user_id, tenant_id, query_text, query_lang, top_doc_ids,
                    fusion_strategy, experiment_id, variant, latency_ms, created_at
                )
                VALUES (
                    :user_id, :tenant_id, :query_text, :query_lang, :top_doc_ids,
                    :fusion_strategy, :experiment_id, :variant, :latency_ms, now()
                )
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "query_text": query_text,
                "query_lang": query_lang,
                "top_doc_ids": top_doc_ids,
                "fusion_strategy": fusion_strategy,
                "experiment_id": experiment_id,
                "variant": variant,
                "latency_ms": latency_ms,
            },
        )

        event_id: int = result.scalar_one()
        metrics.search_events_inserted_total.labels(
            fusion_strategy=fusion_strategy or "unknown"
        ).inc()
        return event_id

    async def insert_click_event(
        self,
        session: AsyncSession,
        *,
        user_id: str | None,
        tenant_id: str,
        query_text: str,
        clicked_doc_id: str,
        position: int | None,
        experiment_id: str | None,
        variant: str | None = None,
    ) -> None:
        """Insert a click event into the appropriate daily partition.

        Args:
            session: Async session
            user_id: User UUID (optional)
            tenant_id: Tenant UUID
            query_text: Search query text (may be hashed if PII enabled)
            clicked_doc_id: Clicked document UUID
            position: Position in results (1-based, optional)
            experiment_id: A/B test experiment ID (optional)
            variant: Recomputed A/B variant name (optional, PR-03)
        """
        # Hash query text if PII protection is enabled
        if self.config.hash_query_text:
            query_hash = hashlib.sha256(query_text.encode()).hexdigest()
            query_text = f"sha256:{query_hash}"

        # Set statement timeout for click inserts (performance optimization)
        await session.execute(
            text(f"SET LOCAL statement_timeout = {self.config.click_statement_timeout_ms}")
        )

        # Insert click event
        await session.execute(
            text(
                """
                INSERT INTO search_events (
                    user_id, tenant_id, query_text, clicked_doc_id, position,
                    experiment_id, variant, created_at
                )
                VALUES (
                    :user_id, :tenant_id, :query_text, :clicked_doc_id, :position,
                    :experiment_id, :variant, now()
                )
                """
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "query_text": query_text,
                "clicked_doc_id": clicked_doc_id,
                "position": position,
                "experiment_id": experiment_id,
                "variant": variant,
            },
        )

        metrics.search_events_clicks_total.inc()

    async def create_daily_partition(self, session: AsyncSession, date: datetime) -> str:
        """Create a daily partition for the given date if it doesn't exist.

        Args:
            session: Async session
            date: Date for the partition (date part only, time is ignored)

        Returns:
            Partition name (e.g., "search_events_20241220")
        """
        partition_name = f"search_events_{date.strftime('%Y%m%d')}"

        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF search_events
                FOR VALUES FROM ('{date.strftime("%Y-%m-%d")}') TO ('{(date + timedelta(days=1)).strftime("%Y-%m-%d")}')
                """
            )
        )

        # Create indexes on the new partition
        await session.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS ix_{partition_name}_user_time_idx
                ON {partition_name} (user_id, created_at DESC)
                """
            )
        )
        await session.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS ix_{partition_name}_exp_idx
                ON {partition_name} (experiment_id, created_at DESC)
                """
            )
        )

        return partition_name

    async def list_partitions(self, session: AsyncSession) -> list[tuple[str, datetime]]:
        """List all daily partitions (excluding the default partition).

        Args:
            session: Async session

        Returns:
            List of (partition_name, upper_bound) tuples
        """
        result = await session.execute(
            text(
                """
                SELECT
                    p.relname as partition_name,
                    pg_get_expr(c.relpartbound, c.oid) as bound_expr
                FROM pg_inherits i
                JOIN pg_class p ON i.inhrelid = p.oid
                JOIN pg_class c ON i.inhparent = c.oid
                WHERE c.relname = 'search_events'
                AND p.relname != 'search_events_default'
                ORDER BY p.relname
                """
            )
        )

        partitions = []
        for row in result.fetchall():
            partition_name = row.partition_name

            # Extract upper bound from the bound expression
            # Format: 'FROM (date) TO (date+1day)'
            bound_expr = row.bound_expr
            if "TO (" in bound_expr:
                # Extract date between TO ( and )
                start = bound_expr.find("TO ('") + 5
                end = bound_expr.find("')", start)
                if start > 4 and end > start:
                    date_str = bound_expr[start:end]
                    upper_bound = datetime.strptime(date_str, "%Y-%m-%d")
                    partitions.append((partition_name, upper_bound))

        return partitions

    async def drop_partition_older_than(
        self, session: AsyncSession, days: int, *, now: datetime | None = None
    ) -> list[str]:
        """Drop partitions older than the specified number of days.

        Args:
            session: Async session
            days: Number of days to retain
            now: Current datetime (for testing)

        Returns:
            List of dropped partition names
        """
        if now is None:
            now = datetime.now()

        cutoff_date = now - timedelta(days=days)

        # Get partitions older than cutoff
        partitions = await self.list_partitions(session)
        dropped_partitions = []

        for partition_name, upper_bound in partitions:
            if upper_bound < cutoff_date:
                await session.execute(text(f"DROP TABLE {partition_name}"))
                dropped_partitions.append(partition_name)
                metrics.search_events_partitions_dropped_total.inc()

        return dropped_partitions

    async def archive_partition_to_s3(
        self, session: AsyncSession, partition_name: str, s3_bucket: str
    ) -> str:
        """Archive a partition to S3 as Parquet and return the S3 key.

        Args:
            session: Async session
            partition_name: Partition name to archive
            s3_bucket: S3 bucket name

        Returns:
            S3 key (e.g., "search-events/20241220.parquet")
        """
        # Query all data from the partition
        result = await session.execute(text(f"SELECT * FROM {partition_name}"))
        rows = result.fetchall()

        if not rows:
            # Empty partition - nothing to archive
            return f"search-events/{partition_name.replace('search_events_', '')}.parquet"

        # Convert rows to Arrow Table
        # Define schema based on the SearchEvent model
        arrow_schema = pa.schema(
            [
                ("id", pa.int64()),
                ("user_id", pa.string()),
                ("tenant_id", pa.string()),
                ("query_text", pa.string()),
                ("query_lang", pa.string()),
                ("top_doc_ids", pa.list_(pa.string())),
                ("clicked_doc_id", pa.string()),
                ("position", pa.int32()),
                ("fusion_strategy", pa.string()),
                ("experiment_id", pa.string()),
                ("variant", pa.string()),
                ("latency_ms", pa.int32()),
                ("created_at", pa.timestamp("ns")),
            ]
        )

        # Convert rows to list of dictionaries
        records = []
        for row in rows:
            record = {
                "id": row.id,
                "user_id": str(row.user_id) if row.user_id else None,
                "tenant_id": str(row.tenant_id),
                "query_text": row.query_text,
                "query_lang": row.query_lang,
                "top_doc_ids": [str(doc_id) for doc_id in row.top_doc_ids]
                if row.top_doc_ids
                else [],
                "clicked_doc_id": str(row.clicked_doc_id) if row.clicked_doc_id else None,
                "position": row.position,
                "fusion_strategy": row.fusion_strategy,
                "experiment_id": row.experiment_id,
                "variant": row.variant,
                "latency_ms": row.latency_ms,
                "created_at": row.created_at,
            }
            records.append(record)

        # Create Arrow Table
        table = Table.from_pylist(records, schema=arrow_schema)

        # Write to Parquet in memory
        import io

        parquet_buffer = io.BytesIO()
        pq.write_table(table, parquet_buffer)
        parquet_bytes = parquet_buffer.getvalue()

        # Upload to S3 using boto3 (sync in thread to avoid blocking)
        s3_client = boto3.client(
            "s3",
            endpoint_url=self.config.s3_endpoint_url,
            aws_access_key_id=self.config.s3_access_key,
            aws_secret_access_key=self.config.s3_secret_key,
        )

        # Ensure bucket exists
        try:
            s3_client.head_bucket(Bucket=s3_bucket)
        except Exception:
            # Bucket doesn't exist, create it
            s3_client.create_bucket(Bucket=s3_bucket)

        # Generate S3 key
        date_str = partition_name.replace("search_events_", "")
        s3_key = f"search-events/{date_str}.parquet"

        # Upload Parquet file
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=parquet_bytes,
            ContentType="application/octet-stream",
        )

        metrics.search_events_partitions_archived_total.inc()
        return s3_key

    async def drop_partition(self, session: AsyncSession, partition_name: str) -> None:
        """Drop a partition table.

        Args:
            session: Async session
            partition_name: Partition name to drop
        """
        # Guard against dropping the default partition
        if partition_name == "search_events_default":
            raise ValueError("Cannot drop the default partition")

        await session.execute(text(f"DROP TABLE {partition_name}"))
        metrics.search_events_partitions_dropped_total.inc()


# Global instance for convenience
search_event_queries = SearchEventQueries(SearchEventsConfig())
