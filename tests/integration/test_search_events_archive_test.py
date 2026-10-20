"""PR-02: POST /search/click integration tests.

Tests the click logging endpoint with PG testcontainer and validates that
click events are properly inserted into the search_events table.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.slow
async def test_log_click_event(wired_app) -> None:
    """POST /search/click with click data → event logged in database."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    clicked_doc_id = str(uuid.uuid4())

    # Index a document first
    index_body = {
        "tenant_id": tenant_id,
        "external_ref": "ref-click-1",
        "title": "Test document",
        "content": "This is a test document for click logging",
        "tags": ["test"],
        "attributes": {"category": "test"},
    }
    index_resp = await client.post("/index", json=index_body)
    assert index_resp.status_code == 201

    # Log a click event
    click_body = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "query": "test query",
        "clicked_doc_id": clicked_doc_id,
        "position": 1,
        "experiment_id": "test-experiment",
    }
    resp = await client.post("/search/click", json=click_body)
    assert resp.status_code == 200
    assert resp.json() == {"logged": True}

    # Verify the click event was inserted into the database
    async with wrapper.session() as session:
        result = await session.execute(
            "SELECT * FROM search_events WHERE user_id = :user_id AND clicked_doc_id = :clicked_doc_id",
            {"user_id": user_id, "clicked_doc_id": clicked_doc_id},
        )
        row = result.fetchone()
        assert row is not None
        assert row.query_text == "test query"
        assert row.position == 1
        assert row.experiment_id == "test-experiment"


@pytest.mark.slow
async def test_log_click_event_no_user(wired_app) -> None:
    """POST /search/click without user_id → event logged without personalization."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    clicked_doc_id = str(uuid.uuid4())

    # Index a document first
    index_body = {
        "tenant_id": tenant_id,
        "external_ref": "ref-click-2",
        "title": "Test document 2",
        "content": "Another test document",
        "tags": ["test"],
    }
    index_resp = await client.post("/index", json=index_body)
    assert index_resp.status_code == 201

    # Log a click event without user_id
    click_body = {
        "tenant_id": tenant_id,
        "query": "test query 2",
        "clicked_doc_id": clicked_doc_id,
        "position": 2,
    }
    resp = await client.post("/search/click", json=click_body)
    assert resp.status_code == 200
    assert resp.json() == {"logged": True}

    # Verify the click event was inserted
    async with wrapper.session() as session:
        result = await session.execute(
            "SELECT * FROM search_events WHERE clicked_doc_id = :clicked_doc_id",
            {"clicked_doc_id": clicked_doc_id},
        )
        row = result.fetchone()
        assert row is not None
        assert row.user_id is None
        assert row.query_text == "test query 2"
        assert row.position == 2


@pytest.mark.slow
async def test_log_click_with_personalization(wired_app, personalization_service) -> None:
    """POST /search/click with user_id → personalization update triggered."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    clicked_doc_id = str(uuid.uuid4())

    # Mock the personalization service
    with patch.object(personalization_service, "update_on_click", new_callable=AsyncMock) as mock_update:
        # Index a document first
        index_body = {
            "tenant_id": tenant_id,
            "external_ref": "ref-click-3",
            "title": "Test document 3",
            "content": "Personalization test document",
            "tags": ["test"],
        }
        index_resp = await client.post("/index", json=index_body)
        assert index_resp.status_code == 201

        # Log a click event with user_id
        click_body = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "query": "personalized query",
            "clicked_doc_id": clicked_doc_id,
            "position": 1,
        }
        resp = await client.post("/search/click", json=click_body)
        assert resp.status_code == 200
        assert resp.json() == {"logged": True}

        # Verify personalization update was called
        mock_update.assert_called_once_with(
            user_id=user_id,
            tenant_id=tenant_id,
            clicked_doc_id=clicked_doc_id,
            query="personalized query",
        )


@pytest.mark.slow
async def test_log_click_error_handling(wired_app) -> None:
    """POST /search/click with invalid data → 422 validation error."""
    wrapper, client = wired_app

    # Test missing tenant_id
    click_body = {
        "user_id": str(uuid.uuid4()),
        "query": "test query",
        "clicked_doc_id": str(uuid.uuid4()),
    }
    resp = await client.post("/search/click", json=click_body)
    assert resp.status_code == 422

    # Test missing clicked_doc_id
    click_body = {
        "user_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "query": "test query",
    }
    resp = await client.post("/search/click", json=click_body)
    assert resp.status_code == 422

    # Test invalid UUID format
    click_body = {
        "user_id": "invalid-uuid",
        "tenant_id": str(uuid.uuid4()),
        "query": "test query",
        "clicked_doc_id": str(uuid.uuid4()),
    }
    resp = await client.post("/search/click", json=click_body)
    assert resp.status_code == 422


@pytest.mark.slow
async def test_search_event_logging_integration(wired_app) -> None:
    """Integration test for search event logging after search requests."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    index_body = {
        "tenant_id": tenant_id,
        "external_ref": "ref-search-1",
        "title": "Search test document",
        "content": "This document will be found by search",
        "tags": ["search"],
    }
    index_resp = await client.post("/index", json=index_body)
    assert index_resp.status_code == 201

    # Perform a search
    search_body = {
        "query": "search test",
        "tenant_id": tenant_id,
        "top_k": 5,
    }
    search_resp = await client.post("/search", json=search_body)
    assert search_resp.status_code == 200

    # Verify a search event was logged
    async with wrapper.session() as session:
        result = await session.execute(
            "SELECT * FROM search_events WHERE tenant_id = :tenant_id ORDER BY created_at DESC LIMIT 1",
            {"tenant_id": tenant_id},
        )
        row = result.fetchone()
        assert row is not None
        assert row.query_text == "search test"
        assert row.tenant_id == uuid.UUID(tenant_id)
        assert row.top_doc_ids is not None
        assert len(row.top_doc_ids) > 0
        assert row.fusion_strategy is not None
        assert row.latency_ms is not None
        assert row.latency_ms > 0


@pytest.mark.slow
async def test_partition_management_integration(wired_app) -> None:
    """Test partition management integration with database."""
    wrapper, client = wired_app

    # Test that partitions exist after migration
    async with wrapper.session() as session:
        # List partitions
        result = await session.execute(
            """
            SELECT p.relname as partition_name
            FROM pg_inherits i
            JOIN pg_class p ON i.inhrelid = p.oid
            JOIN pg_class c ON i.inhparent = c.oid
            WHERE c.relname = 'search_events'
            AND p.relname != 'search_events_default'
            ORDER BY p.relname
            """
        )
        partitions = [row.partition_name for row in result.fetchall()]

        # Should have partitions (exact count depends on migration)
        assert len(partitions) > 0

        # Verify partition naming convention
        for partition_name in partitions:
            assert partition_name.startswith("search_events_")
            assert len(partition_name) == 19  # "search_events_" + 8 digits (YYYYMMDD)
