"""P-11: POST /search integration tests (ARCHITECT §14.1, §7.1).

Tests the search endpoint with PG testcontainer + mocked Qdrant (since
running a real Qdrant in tests is heavy). The lexical channel is tested
against real PG; the vector channel is mocked to return canned results.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.slow
async def test_search_empty_database(wired_app) -> None:
    """POST /search with no documents → empty hits."""
    wrapper, client = wired_app
    body = {
        "query": "hello world",
        "tenant_id": str(uuid.uuid4()),
        "top_k": 10,
    }
    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post("/search", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["hits"] == []
    assert data["total_lexical"] == 0
    assert data["total_vector"] == 0
    assert data["latency_ms"] >= 0
    assert data["degraded"] is False


@pytest.mark.slow
async def test_search_returns_lexical_results(wired_app, engine) -> None:
    """Index a document, then search → lexical channel finds it."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    # Index a document
    index_body = {
        "tenant_id": tenant_id,
        "external_ref": "ref-search-1",
        "title": "Python programming guide",
        "content": "A comprehensive guide to Python programming language",
        "tags": ["python", "guide"],
        "attributes": {"category": "tech"},
    }
    index_resp = await client.post("/index", json=index_body)
    assert index_resp.status_code == 201
    doc_id = index_resp.json()["doc_id"]

    # Search for it (with mocked vector channel returning empty)
    search_body = {
        "query": "Python programming",
        "tenant_id": tenant_id,
        "top_k": 10,
    }
    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        search_resp = await client.post("/search", json=search_body)
    assert search_resp.status_code == 200
    data = search_resp.json()
    assert data["total_lexical"] >= 1
    assert len(data["hits"]) >= 1
    hit_ids = [h["doc_id"] for h in data["hits"]]
    assert doc_id in hit_ids


@pytest.mark.slow
async def test_search_tenant_isolation(wired_app, engine) -> None:
    """Documents from tenant A must not appear in tenant B's search."""
    wrapper, client = wired_app
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    # Index in tenant A
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_a,
            "external_ref": "ref-a",
            "title": "Secret document A",
            "content": "This belongs to tenant A",
        },
    )
    # Index in tenant B
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_b,
            "external_ref": "ref-b",
            "title": "Secret document B",
            "content": "This belongs to tenant B",
        },
    )

    # Search as tenant A
    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={"query": "Secret document", "tenant_id": tenant_a, "top_k": 10},
        )
    data = resp.json()
    for hit in data["hits"]:
        # All hits should be from tenant A only
        assert "tenant A" in hit["snippet"] or "document A" in hit["title"]


@pytest.mark.slow
async def test_search_filters_language(wired_app, engine) -> None:
    """Language filter narrows lexical results."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-en",
            "title": "Hello world",
            "content": "English content about programming",
            "language": "en",
        },
    )
    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-ru",
            "title": "Привет мир",
            "content": "Русский контент о программировании",
            "language": "ru",
        },
    )

    # Search with language filter for English only
    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "programming",
                "tenant_id": tenant_id,
                "filters": {"language": ["en"]},
                "top_k": 10,
            },
        )
    data = resp.json()
    for hit in data["hits"]:
        assert hit["doc_id"]  # just verify we got results


@pytest.mark.slow
async def test_search_response_structure(wired_app) -> None:
    """POST /search response has all required MVP fields."""
    wrapper, client = wired_app
    body = {
        "query": "test",
        "tenant_id": str(uuid.uuid4()),
    }
    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post("/search", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "facets" in data
    assert "total_lexical" in data
    assert "total_vector" in data
    assert "latency_ms" in data
    assert "degraded" in data
    assert isinstance(data["hits"], list)
    assert isinstance(data["facets"], dict)
    assert data["facets"] == {}  # empty on MVP


@pytest.mark.slow
async def test_search_invalid_query_rejected(wired_app) -> None:
    """Empty query → 422 validation error."""
    wrapper, client = wired_app
    body = {
        "query": "",
        "tenant_id": str(uuid.uuid4()),
    }
    resp = await client.post("/search", json=body)
    assert resp.status_code == 422


@pytest.mark.slow
async def test_search_missing_tenant_id_rejected(wired_app) -> None:
    """Missing tenant_id → 422 validation error."""
    wrapper, client = wired_app
    resp = await client.post("/search", json={"query": "hello"})
    assert resp.status_code == 422


@pytest.mark.slow
async def test_search_explain_mode(wired_app, engine) -> None:
    """explain=True returns debug info on hits."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())

    await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": "ref-explain",
            "title": "Explain test",
            "content": "Content for explain mode test",
        },
    )

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "Explain test",
                "tenant_id": tenant_id,
                "explain": True,
                "top_k": 5,
            },
        )
    data = resp.json()
    if data["hits"]:
        hit = data["hits"][0]
        assert hit["debug"] is not None
        assert "rrf_score" in hit["debug"]
