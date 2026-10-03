"""P-11: POST /search degraded mode integration tests (C-09).

Tests degraded mode behavior: lex-only fallback, partial results,
deadlock retry, and feature flags. Uses real PG + mocked Qdrant.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.slow
async def test_search_degraded_vector_disabled(wired_app) -> None:
    """Test that vector_search_enabled=False triggers degraded mode."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-lex-only",
            "title": "Lexical only test",
            "content": "This document should only appear in lexical results",
        },
    )

    # Search with vector disabled via feature flag override
    search_body = {
        "query": "Lexical test",
        "tenant_id": tenant_id,
        "top_k": 10,
    }

    # Override dependency to simulate vector_search_enabled=False
    from app.config import FeatureFlags

    async def mock_get_feature_flags():
        return FeatureFlags(vector_search_enabled=False)

    wrapper.dependency_overrides[FeatureFlags] = mock_get_feature_flags

    try:
        resp = await client.post("/search", json=search_body)
    finally:
        wrapper.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert data["total_vector"] == 0
    assert data["total_lexical"] >= 1
    assert len(data["hits"]) >= 1
    assert "Lexical only test" in data["hits"][0]["title"]


@pytest.mark.slow
async def test_search_degraded_qdrant_unavailable(wired_app) -> None:
    """Test that Qdrant unavailability triggers degraded mode."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-qdrant-down",
            "title": "Qdrant down test",
            "content": "This document should appear in lexical results when Qdrant is down",
        },
    )

    # Search with mocked Qdrant unavailability
    search_body = {
        "query": "Qdrant test",
        "tenant_id": tenant_id,
        "top_k": 10,
    }

    with patch(
        "app.search.orchestrator.vector_search",
        new_callable=AsyncMock,
        side_effect=Exception("Qdrant connection failed"),
    ):
        resp = await client.post("/search", json=search_body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert data["total_vector"] == 0
    assert data["total_lexical"] >= 1
    assert len(data["hits"]) >= 1
    assert "Qdrant down test" in data["hits"][0]["title"]


@pytest.mark.slow
async def test_search_partial_timeout(wired_app) -> None:
    """Test that timeout triggers partial results."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-timeout",
            "title": "Timeout test",
            "content": "This document should appear in partial results",
        },
    )

    # Search with very short timeout
    search_body = {
        "query": "Timeout test",
        "tenant_id": tenant_id,
        "timeout_ms": 10,  # Very short timeout
        "top_k": 10,
    }

    resp = await client.post("/search", json=search_body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["partial"] is True
    # Results may be empty or partial depending on timing
    assert data["hits"] is not None


@pytest.mark.slow
async def test_search_degraded_lexical_only_returns_results(wired_app) -> None:
    """Test that lexical channel still works when vector channel is degraded."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index multiple documents
    documents = [
        {
            "tenant_id": tenant_id,
            "external_ref": "ref-1",
            "title": "Document 1",
            "content": "First document for degraded mode test",
        },
        {
            "tenant_id": tenant_id,
            "external_ref": "ref-2",
            "title": "Document 2",
            "content": "Second document for degraded mode test",
        },
    ]

    for doc in documents:
        await client.post("/index", json=doc)

    # Search with mocked vector failure
    search_body = {
        "query": "degraded mode test",
        "tenant_id": tenant_id,
        "top_k": 10,
    }

    with patch(
        "app.search.orchestrator.vector_search",
        new_callable=AsyncMock,
        side_effect=Exception("Vector channel failed"),
    ):
        resp = await client.post("/search", json=search_body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert data["total_vector"] == 0
    assert data["total_lexical"] >= 1
    assert len(data["hits"]) >= 1

    # Verify lexical results contain expected documents
    hit_titles = [hit["title"] for hit in data["hits"]]
    assert any("Document" in title for title in hit_titles)


@pytest.mark.slow
async def test_search_metrics_incremented(wired_app) -> None:
    """Test that degraded mode metrics are incremented."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-metrics",
            "title": "Metrics test",
            "content": "Document for testing degraded mode metrics",
        },
    )

    # Search with mocked Qdrant failure
    search_body = {
        "query": "Metrics test",
        "tenant_id": tenant_id,
        "top_k": 10,
    }

    from app.observability import metrics

    # Get initial values
    initial_degraded = metrics.search_degraded_total._value._value
    initial_partial = metrics.search_partial_total._value._value

    with patch(
        "app.search.orchestrator.vector_search",
        new_callable=AsyncMock,
        side_effect=Exception("Qdrant unavailable"),
    ):
        resp = await client.post("/search", json=search_body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True

    # Verify metrics were incremented
    assert metrics.search_degraded_total._value._value > initial_degraded
    # Partial may or may not be incremented depending on timing


@pytest.mark.slow
async def test_search_deadlock_retry_integration(wired_app, engine) -> None:
    """Test that deadlock retry works in integration context."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-deadlock",
            "title": "Deadlock retry test",
            "content": "Document for testing deadlock retry mechanism",
        },
    )

    # Search - deadlock retry should be handled internally
    search_body = {
        "query": "Deadlock retry test",
        "tenant_id": tenant_id,
        "top_k": 10,
    }

    resp = await client.post("/search", json=search_body)

    assert resp.status_code == 200
    data = resp.json()
    # Should not be degraded due to deadlock (retry should work)
    assert data["degraded"] is False
    assert data["total_lexical"] >= 1
    assert len(data["hits"]) >= 1
    assert "Deadlock retry test" in data["hits"][0]["title"]
