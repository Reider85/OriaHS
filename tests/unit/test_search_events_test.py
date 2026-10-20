"""Unit tests for SearchEventsQueries (PR-02).

Tests partition management, S3 archiving, and event insertion with mocked
boto3 client and PG testcontainer. All database operations are async and
properly isolated.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import SearchEventsConfig
from app.db.queries.search_events import SearchEventQueries


@pytest.fixture
def mock_session():
    """Mock async session for database operations."""
    session = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_config():
    """Search events config with test values."""
    return SearchEventsConfig(
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="test-bucket",
        s3_access_key="test-access-key",
        s3_secret_key="test-secret-key",
        retention_days=90,
        partition_ahead_days=7,
        migration_partition_days=30,
        hash_query_text=False,
        click_statement_timeout_ms=100,
    )


@pytest.fixture
def search_event_queries(mock_config):
    """SearchEventQueries instance with mocked config."""
    return SearchEventQueries(mock_config)


class TestSearchEventQueries:
    """Test suite for SearchEventQueries methods."""

    @pytest.mark.asyncio
    async def test_insert_search_event(self, mock_session, search_event_queries):
        """Test inserting a search event into the database."""
        # Mock the execute call to return an ID
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 123
        mock_session.execute.return_value = mock_result

        # Call the method
        event_id = await search_event_queries.insert_search_event(
            session=mock_session,
            user_id="user-123",
            tenant_id="tenant-123",
            query_text="test query",
            query_lang="en",
            top_doc_ids=["doc-1", "doc-2", "doc-3"],
            fusion_strategy="rrf",
            experiment_id="test-experiment",
            variant="A",
            latency_ms=150,
        )

        # Verify the result
        assert event_id == 123

        # Verify the execute call was made with correct parameters
        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        assert (
            call_args[0][0].text
            == """
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
        )

    @pytest.mark.asyncio
    async def test_insert_search_event_hashed(self, mock_session, search_event_queries):
        """Test inserting a search event with query text hashing."""
        # Enable hashing
        search_event_queries.config.hash_query_text = True

        # Mock the execute call
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 123
        mock_session.execute.return_value = mock_result

        # Call the method
        await search_event_queries.insert_search_event(
            session=mock_session,
            user_id="user-123",
            tenant_id="tenant-123",
            query_text="test query",
            query_lang="en",
            top_doc_ids=["doc-1", "doc-2", "doc-3"],
            fusion_strategy="rrf",
            experiment_id="test-experiment",
            latency_ms=150,
        )

        # Verify the query text was hashed
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["query_text"].startswith("sha256:")
        assert len(params["query_text"]) == 71  # "sha256:" + 64 hex chars

    @pytest.mark.asyncio
    async def test_insert_click_event(self, mock_session, search_event_queries):
        """Test inserting a click event into the database."""
        # Mock the execute call
        mock_session.execute = AsyncMock()

        # Call the method
        await search_event_queries.insert_click_event(
            session=mock_session,
            user_id="user-123",
            tenant_id="tenant-123",
            query_text="test query",
            clicked_doc_id="doc-456",
            position=3,
            experiment_id="test-experiment",
            variant="B",
        )

        # Verify the execute call was made (SET LOCAL + INSERT)
        assert mock_session.execute.call_count == 2
        call_args = mock_session.execute.call_args
        assert (
            call_args[0][0].text
            == """
                INSERT INTO search_events (
                    user_id, tenant_id, query_text, clicked_doc_id, position,
                    experiment_id, variant, created_at
                )
                VALUES (
                    :user_id, :tenant_id, :query_text, :clicked_doc_id, :position,
                    :experiment_id, :variant, now()
                )
                """
        )

    @pytest.mark.asyncio
    async def test_create_daily_partition(self, mock_session, search_event_queries):
        """Test creating a daily partition."""
        test_date = datetime(2024, 12, 20)

        # Mock the execute call
        mock_session.execute = AsyncMock()

        # Call the method
        partition_name = await search_event_queries.create_daily_partition(
            session=mock_session, date=test_date
        )

        # Verify the result
        assert partition_name == "search_events_20241220"

        # Verify the execute calls were made
        assert mock_session.execute.call_count == 3  # CREATE TABLE + 2 indexes

    @pytest.mark.asyncio
    async def test_list_partitions(self, mock_session, search_event_queries):
        """Test listing all partitions."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            SimpleNamespace(
                partition_name="search_events_20241220",
                bound_expr="FROM ('2024-12-20') TO ('2024-12-21')",
            ),
            SimpleNamespace(
                partition_name="search_events_20241219",
                bound_expr="FROM ('2024-12-19') TO ('2024-12-20')",
            ),
        ]
        mock_session.execute.return_value = mock_result

        # Call the method
        partitions = await search_event_queries.list_partitions(mock_session)

        # Verify the result
        assert len(partitions) == 2
        assert partitions[0] == ("search_events_20241220", datetime(2024, 12, 21))
        assert partitions[1] == ("search_events_20241219", datetime(2024, 12, 20))

    @pytest.mark.asyncio
    async def test_drop_partition_older_than(self, mock_session, search_event_queries):
        """Test dropping partitions older than specified days."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            SimpleNamespace(
                partition_name="search_events_20241220",
                bound_expr="FROM ('2024-12-20') TO ('2024-12-21')",
            ),  # Recent
            SimpleNamespace(
                partition_name="search_events_20241201",
                bound_expr="FROM ('2024-12-01') TO ('2024-12-02')",
            ),  # Old
        ]
        mock_session.execute.return_value = mock_result

        # Call the method (cutoff = 2025-01-15 - 30d = 2024-12-16)
        dropped = await search_event_queries.drop_partition_older_than(
            mock_session, days=30, now=datetime(2025, 1, 15)
        )

        # Verify the result
        assert dropped == ["search_events_20241201"]

    @pytest.mark.asyncio
    @patch("boto3.client")
    async def test_archive_partition_to_s3(
        self, mock_boto3_client, mock_session, search_event_queries
    ):
        """Test archiving a partition to S3."""
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto3_client.return_value = mock_s3
        mock_s3.head_bucket.side_effect = Exception("Bucket not found")  # Force bucket creation

        # Mock database query — archive rows are attribute-accessed (row.id, ...)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            SimpleNamespace(
                id=1,
                user_id="user-123",
                tenant_id="tenant-123",
                query_text="test query",
                query_lang="en",
                top_doc_ids=["doc-1", "doc-2"],
                clicked_doc_id=None,
                position=None,
                fusion_strategy="rrf",
                experiment_id="test-experiment",
                variant="A",
                latency_ms=150,
                created_at=datetime(2024, 12, 20, 10, 30, 0),
            )
        ]
        mock_session.execute.return_value = mock_result

        # Call the method
        s3_key = await search_event_queries.archive_partition_to_s3(
            mock_session, "search_events_20241220", "test-bucket"
        )

        # Verify the result
        assert s3_key == "search-events/20241220.parquet"

        # Verify S3 operations
        mock_s3.put_object.assert_called_once()
        call_args = mock_s3.put_object.call_args
        assert call_args[1]["Bucket"] == "test-bucket"
        assert call_args[1]["Key"] == "search-events/20241220.parquet"
        assert call_args[1]["ContentType"] == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_archive_partition_empty(self, mock_session, search_event_queries):
        """Test archiving an empty partition."""
        # Mock empty database query
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        # Call the method
        s3_key = await search_event_queries.archive_partition_to_s3(
            mock_session, "search_events_20241220", "test-bucket"
        )

        # Verify the result for empty partition
        assert s3_key == "search-events/20241220.parquet"

    @pytest.mark.asyncio
    async def test_drop_partition(self, mock_session, search_event_queries):
        """Test dropping a partition."""
        # Mock the execute call
        mock_session.execute = AsyncMock()

        # Call the method
        await search_event_queries.drop_partition(mock_session, "search_events_20241220")

        # Verify the execute call
        mock_session.execute.assert_called_once()
        assert mock_session.execute.call_args[0][0].text == "DROP TABLE search_events_20241220"

    @pytest.mark.asyncio
    async def test_drop_partition_default_guard(self, mock_session, search_event_queries):
        """Test that dropping the default partition is prevented."""
        # Mock the execute call
        mock_session.execute = AsyncMock()

        # Call the method with default partition (should raise exception)
        with pytest.raises(ValueError):
            await search_event_queries.drop_partition(mock_session, "search_events_default")

        # Verify no execute call was made
        mock_session.execute.assert_not_called()
