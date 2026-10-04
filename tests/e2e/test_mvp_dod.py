"""MVP Definition of Done (DoD) End-to-End Tests (P-17).

Implements all 6 DoD criteria from ROADMAP §3.4 to validate the complete MVP
workflow from indexing to search with reconciler recovery, metrics, and
fast-path behavior.

These tests require the full stack (PG + Qdrant + Redis + API) running via
docker-compose or testcontainers.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.e2e.conftest import (
    E2ETestClient,
    reconcile_wait,
    validate_dod_criteria_1,
    validate_dod_criteria_2,
    validate_dod_criteria_4,
    validate_dod_criteria_6,
)


@pytest.mark.slow
async def test_dod_1_index_search_workflow(wired_app_with_qdrant, sample_index_data, sample_search_query):
    """DoD #1: POST /index creates document and within 30 sec available via POST /search.

    Validates the complete indexing → search pipeline within the MVP SLO.
    """
    engine, client, worker, _ = wired_app_with_qdrant
    e2e_client = E2ETestClient(client)

    # Index document
    index_response = await e2e_client.index_document(sample_index_data)
    validate_dod_criteria_1(index_response, index_response["indexed_at"])
    doc_id = index_response["doc_id"]

    # Wait for reconciler to process (up to 30 seconds)
    async with reconcile_wait(max_seconds=30, session_factory=lambda: async_sessionmaker(bind=engine, expire_on_commit=False)()):
        pass

    # Search for the document
    search_query = sample_search_query.copy()
    search_query["tenant_id"] = sample_index_data["tenant_id"]
    search_response = await e2e_client.search_documents(search_query)

    # Validate search results
    validate_dod_criteria_2(search_response, doc_id)

    # Verify document is in database
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE id = :doc_id AND deleted_at IS NULL"),
            {"doc_id": doc_id},
        )
        count = result.scalar_one()
        assert count == 1, "Document not found in database"


@pytest.mark.slow
async def test_dod_2_rrf_fusion_deduplication(wired_app_with_qdrant, sample_index_data, sample_search_query):
    """DoD #2: POST /search returns RRF-fused results with proper deduplication.

    Validates that RRF fusion works correctly and duplicate documents are
    properly handled in the search results.
    """
    engine, client, worker, _ = wired_app_with_qdrant
    e2e_client = E2ETestClient(client)

    # Index two documents with similar content
    tenant_id = str(uuid4())
    data1 = sample_index_data.copy()
    data1["tenant_id"] = tenant_id
    data1["external_ref"] = "doc1"
    data1["content"] = "Machine learning is a subset of artificial intelligence."

    data2 = sample_index_data.copy()
    data2["tenant_id"] = tenant_id
    data2["external_ref"] = "doc2"
    data2["content"] = "Artificial intelligence includes machine learning."

    # Index both documents
    resp1 = await e2e_client.index_document(data1)
    resp2 = await e2e_client.index_document(data2)

    # Wait for reconciler
    async with reconcile_wait(max_seconds=30, session_factory=lambda: async_sessionmaker(bind=engine, expire_on_commit=False)()):
        pass

    # Search for AI-related content
    search_query = sample_search_query.copy()
    search_query["tenant_id"] = tenant_id
    search_query["query"] = "artificial intelligence machine learning"
    search_response = await e2e_client.search_documents(search_query)

    # Validate RRF fusion
    validate_dod_criteria_2(search_response, resp1["doc_id"])
    validate_dod_criteria_2(search_response, resp2["doc_id"])

    # Check that we get exactly 2 results (no duplicates)
    assert len(search_response["hits"]) == 2, (
        f"Expected 2 results, got {len(search_response['hits'])}"
    )

    # Check that results are sorted by score (RRF should have fused them)
    scores = [hit["score"] for hit in search_response["hits"]]
    assert scores == sorted(scores, reverse=True), "Results should be sorted by score"


@pytest.mark.slow
async def test_dod_3_reconciler_recovery(wired_app_with_qdrant, sample_index_data):
    """DoD #3: Reconciler recovers when Qdrant is unavailable.

    Validates that the reconciler can process outbox entries when Qdrant
    becomes unavailable and recovers when it comes back online.
    """
    engine, client, worker, _ = wired_app_with_qdrant
    e2e_client = E2ETestClient(client)

    # Index a document
    index_response = await e2e_client.index_document(sample_index_data)
    doc_id = index_response["doc_id"]

    # Check that document is in outbox
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM search_outbox WHERE document_id = :doc_id"),
            {"doc_id": doc_id},
        )
        outbox_count = result.scalar_one()
        assert outbox_count == 1, "Document not found in outbox"

    # Get Qdrant container from conftest and stop it
    from testcontainers import DockerClient

    # Find the running Qdrant container
    with DockerClient() as docker_client:
        containers = docker_client.containers.list()
        qdrant_container = None
        for container in containers:
            if "qdrant" in container.name.lower():
                qdrant_container = container
                break

        if qdrant_container:
            print("Stopping Qdrant container...")
            qdrant_container.stop()

            # Wait a bit for the stop to take effect
            await asyncio.sleep(5)

            # Run reconciler - should fail and mark as failed
            processed = await worker.run_once()
            assert processed == 1, "Reconciler should process one row"

            # Check that document is marked as failed
            async with engine.begin() as conn:
                result = await conn.execute(
                    text("SELECT status, attempts FROM search_outbox WHERE document_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                row = result.fetchone()
                assert row is not None, "Outbox row should exist"
                status, attempts = row
                assert status == "failed", f"Expected status 'failed', got '{status}'"
                assert attempts >= 1, f"Expected attempts >= 1, got {attempts}"

            # Start Qdrant container again
            print("Starting Qdrant container...")
            qdrant_container.start()
            await asyncio.sleep(5)  # Wait for Qdrant to be ready

            # Reset next_retry_at to now so it can be claimed
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE search_outbox
                        SET next_retry_at = now(), status = 'pending'
                        WHERE document_id = :doc_id
                    """),
                    {"doc_id": doc_id},
                )

            # Run reconciler again - should succeed
            processed = await worker.run_once()
            assert processed == 1, "Reconciler should process one row"

            # Check that document is marked as done
            async with engine.begin() as conn:
                result = await conn.execute(
                    text("SELECT status FROM search_outbox WHERE document_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                row = result.fetchone()
                assert row is not None, "Outbox row should exist"
                status = row[0]
                assert status == "done", f"Expected status 'done', got '{status}'"
        else:
            # Fallback: Use mock Qdrant client that fails
            from qdrant_client import AsyncQdrantClient

            # Create a mock Qdrant client that always fails
            mock_client = AsyncQdrantClient(host="127.0.0.1", port=1)  # Invalid port

            # Override worker's Qdrant service
            from app.services.qdrant import QdrantService
            mock_service = QdrantService(client=mock_client, collection_name="documents")

            # Temporarily replace the worker's Qdrant service
            original_qdrant = worker._qdrant
            worker._qdrant = mock_service

            try:
                # Run reconciler - should fail
                processed = await worker.run_once()
                assert processed == 1, "Reconciler should process one row"

                # Check that document is marked as failed
                async with engine.begin() as conn:
                    result = await conn.execute(
                        text("SELECT status, attempts FROM search_outbox WHERE document_id = :doc_id"),
                        {"doc_id": doc_id},
                    )
                    row = result.fetchone()
                    assert row is not None, "Outbox row should exist"
                    status, attempts = row
                    assert status == "failed", f"Expected status 'failed', got '{status}'"
                    assert attempts >= 1, f"Expected attempts >= 1, got {attempts}"

                # Restore original Qdrant service
                worker._qdrant = original_qdrant

                # Run reconciler again - should succeed
                processed = await worker.run_once()
                assert processed == 1, "Reconciler should process one row"

                # Check that document is marked as done
                async with engine.begin() as conn:
                    result = await conn.execute(
                        text("SELECT status FROM search_outbox WHERE document_id = :doc_id"),
                        {"doc_id": doc_id},
                    )
                    row = result.fetchone()
                    assert row is not None, "Outbox row should exist"
                    status = row[0]
                    assert status == "done", f"Expected status 'done', got '{status}'"
            finally:
                # Always restore original service
                worker._qdrant = original_qdrant


@pytest.mark.slow
async def test_dod_4_metrics_dashboards(wired_app_with_qdrant, sample_index_data, sample_search_query):
    """DoD #4: Prometheus metrics published and Grafana dashboards display correctly.

    Validates that all required metrics are published and accessible.
    """
    engine, client, worker, _ = wired_app_with_qdrant
    e2e_client = E2ETestClient(client)

    # Perform some operations to generate metrics
    await e2e_client.index_document(sample_index_data)

    # Wait for reconciler to process
    async with reconcile_wait(max_seconds=30, session_factory=lambda: async_sessionmaker(bind=engine, expire_on_commit=False)()):
        pass

    search_query = sample_search_query.copy()
    search_query["tenant_id"] = sample_index_data["tenant_id"]
    await e2e_client.search_documents(search_query)

    # Get health status
    await e2e_client.get_health()

    # Get metrics
    metrics_text = await e2e_client.get_metrics()

    # Validate metrics presence
    validate_dod_criteria_4(metrics_text)

    # Check specific metrics
    assert "search_latency_ms" in metrics_text
    assert "http_requests_total" in metrics_text
    assert "index_lag_seconds" in metrics_text

    # Check that metrics have proper format
    lines = metrics_text.strip().split("\n")
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        # Simple metric format validation
        parts = line.split(" ")
        assert len(parts) >= 2, f"Invalid metric format: {line}"


@pytest.mark.slow
async def test_dod_5_dead_letter_digest(wired_app_with_qdrant):
    """DoD #5: Dead-letter records appear in digest table after max failures.

    Validates that documents that fail to process after max attempts
    appear in the dead-letter digest table.
    """
    engine, client, worker, digest_worker = wired_app_with_qdrant
    e2e_client = E2ETestClient(client)

    # Create a document
    tenant_id = str(uuid4())
    valid_data = {
        "tenant_id": tenant_id,
        "external_ref": "test-doc",
        "title": "Test Document",
        "content": "This is a test document for dead letter validation.",
        "tags": ["test"],
        "attributes": {},
    }

    # Index the document
    index_response = await e2e_client.index_document(valid_data)
    doc_id = index_response["doc_id"]

    # Get the document ID from response
    from uuid import UUID
    doc_uuid = UUID(doc_id)

    # Update the document to have a nonexistent embedding model
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE documents
                SET embedding_model = 'nonexistent-model'
                WHERE id = :doc_id
            """),
            {"doc_id": doc_uuid}
        )

        # Set outbox attempts to max_attempts-1 so it goes dead on next run
        from app.config import settings
        max_attempts = settings.reconciler.max_attempts
        await conn.execute(
            text("""
                UPDATE search_outbox
                SET attempts = :max_attempts_minus_1, status = 'failed', next_retry_at = now()
                WHERE document_id = :doc_id
            """),
            {"doc_id": doc_uuid, "max_attempts_minus_1": max_attempts - 1}
        )

    # Run reconciler - should fail and mark as dead
    processed = await worker.run_once()
    assert processed == 1, "Reconciler should process one row"

    # Check that document is marked as dead
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT status, last_error FROM search_outbox WHERE document_id = :doc_id"),
            {"doc_id": doc_uuid},
        )
        row = result.fetchone()
        assert row is not None, "Outbox row should exist"
        status, last_error = row
        assert status == "dead", f"Expected status 'dead', got '{status}'"
        assert last_error is not None, "Last error should be set"
        assert "model" in last_error.lower() or "embedding" in last_error.lower(), \
            f"Error message should contain 'model' or 'embedding': {last_error}"

    # Run digest worker to aggregate dead letters
    groups = await digest_worker.run_once()
    assert groups >= 1, f"Digest should aggregate at least 1 group, got {groups}"

    # Check that digest table contains the dead letter
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT tenant_id, model_name, error_type, count, sample_doc_ids
                FROM search_outbox_dead_digest
                WHERE tenant_id = :tenant_id AND model_name = :model_name AND error_type = :error_type
            """),
            {
                "tenant_id": tenant_id,
                "model_name": "nonexistent-model",
                "error_type": "embedding_failed"
            }
        )
        row = result.fetchone()
        assert row is not None, "Dead-letter digest row should exist"
        assert row[3] >= 1, f"Count should be >= 1, got {row[3]}"

        # Check that sample_doc_ids contains our document ID
        sample_doc_ids = row[4]  # sample_doc_ids column
        assert doc_uuid in sample_doc_ids, f"Sample doc IDs should contain {doc_uuid}, got {sample_doc_ids}"


@pytest.mark.slow
async def test_dod_6_fast_path_duplicate_content(wired_app_with_qdrant, sample_index_data):
    """DoD #6: Fast-path behavior for duplicate indexing with same content.

    Validates that re-indexing identical content returns the same doc_id
    without creating new outbox entries.
    """
    engine, client, worker, _ = wired_app_with_qdrant
    e2e_client = E2ETestClient(client)

    # Index document first time
    first_response = await e2e_client.index_document(sample_index_data)
    doc_id = first_response["doc_id"]

    # Index same content again (should trigger fast-path)
    second_response = await e2e_client.index_document(sample_index_data)

    # Validate fast-path behavior
    validate_dod_criteria_6(first_response, second_response)
    assert second_response["doc_id"] == doc_id
    assert second_response["status"] == "no_change"

    # Verify no new outbox entry was created
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM search_outbox WHERE document_id = :doc_id"),
            {"doc_id": doc_id},
        )
        outbox_count = result.scalar_one()
        assert outbox_count == 1, "Fast-path should not create new outbox entry"


@pytest.mark.slow
async def test_dod_end_to_end_workflow(wired_app, sample_index_data, sample_search_query):
    """Complete end-to-end workflow test covering all DoD criteria.

    This test validates the entire MVP workflow in one go:
    1. Index document
    2. Wait for reconciler
    3. Search document
    4. Verify metrics
    5. Test fast-path behavior
    """
    engine, client = wired_app
    e2e_client = E2ETestClient(client)

    # Step 1: Index document
    index_response = await e2e_client.index_document(sample_index_data)
    validate_dod_criteria_1(index_response, index_response["indexed_at"])
    doc_id = index_response["doc_id"]

    # Step 2: Wait for reconciler to process
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with reconcile_wait(max_seconds=30, session_factory=session_factory):
        pass

    # Step 3: Search for the document
    search_query = sample_search_query.copy()
    search_query["tenant_id"] = sample_index_data["tenant_id"]
    search_response = await e2e_client.search_documents(search_query)
    validate_dod_criteria_2(search_response, doc_id)

    # Step 4: Check metrics
    metrics_text = await e2e_client.get_metrics()
    validate_dod_criteria_4(metrics_text)

    # Step 5: Test fast-path behavior
    duplicate_response = await e2e_client.index_document(sample_index_data)
    validate_dod_criteria_6(index_response, duplicate_response)

    # Step 6: Verify document persistence in database
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE id = :doc_id AND deleted_at IS NULL"),
            {"doc_id": doc_id},
        )
        count = result.scalar_one()
        assert count == 1, "Document not found in database"

    # Step 7: Verify no duplicate outbox entries
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM search_outbox WHERE document_id = :doc_id"),
            {"doc_id": doc_id},
        )
        outbox_count = result.scalar_one()
        assert outbox_count == 1, "Duplicate outbox entries found"
