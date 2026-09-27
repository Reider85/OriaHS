"""C-08: GET /index/status/{token} — polling for read-your-writes.

Tests the ARCHITECT §14.3 endpoint for checking outbox status via token
(=doc_id). Covers all status transitions: pending → done, dead status, invalid
token handling, and cache control header.
"""

import uuid

import pytest
from sqlalchemy import text

from app.api.schemas import IndexStatusResponse


def _payload(
    external_ref: str = "ref-1", content: str = "Hello world", **overrides: object
) -> dict:
    body = {
        "tenant_id": str(uuid.uuid4()),
        "external_ref": external_ref,
        "title": "Hello",
        "content": content,
        "tags": ["greeting"],
        "attributes": {"category": "test"},
    }
    body.update(overrides)
    return body


@pytest.mark.slow
async def test_status_done(wired_app, engine) -> None:
    """Test GET /index/status/{token} returns 'done' when outbox is completed."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    data = resp.json()
    token = data["wait_for_index_token"]
    doc_id = uuid.UUID(token)
    
    # Manually mark the outbox as done (simulating successful processing)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE search_outbox "
                "SET status = 'done', updated_at = now() "
                "WHERE document_id = :doc_id"
            ),
            {"doc_id": doc_id},
        )
    
    # Check status endpoint
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert IndexStatusResponse(**data)
    assert data["token"] == token
    assert data["status"] == "done"


@pytest.mark.slow
async def test_status_pending(wired_app, engine) -> None:
    """Test GET /index/status/{token} returns 'pending' when outbox is still pending."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    data = resp.json()
    token = data["wait_for_index_token"]
    
    # Check status endpoint immediately (should be pending)
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert IndexStatusResponse(**data)
    assert data["token"] == token
    assert data["status"] == "pending"


@pytest.mark.slow
async def test_status_dead(wired_app, engine) -> None:
    """Test GET /index/status/{token} returns 'dead' when outbox is dead."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    data = resp.json()
    token = data["wait_for_index_token"]
    doc_id = uuid.UUID(token)
    
    # Manually mark the outbox as dead (simulating permanent failure)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE search_outbox "
                "SET status = 'dead', updated_at = now() "
                "WHERE document_id = :doc_id"
            ),
            {"doc_id": doc_id},
        )
    
    # Check status endpoint
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert IndexStatusResponse(**data)
    assert data["token"] == token
    assert data["status"] == "dead"


@pytest.mark.slow
async def test_token_not_found(wired_app) -> None:
    """Test GET /index/status/{token} returns 404 for nonexistent token."""
    _, client = wired_app
    
    # Use a random UUID that doesn't exist
    nonexistent_token = str(uuid.uuid4())
    
    resp = await client.get(f"/index/status/{nonexistent_token}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Token not found"


@pytest.mark.slow
async def test_invalid_uuid(wired_app) -> None:
    """Test GET /index/status/{token} returns 422 for invalid UUID format."""
    _, client = wired_app
    
    resp = await client.get("/index/status/not-a-uuid")
    assert resp.status_code == 422
    assert "Invalid token format" in resp.json()["detail"]


@pytest.mark.slow
async def test_cache_control_header(wired_app, engine) -> None:
    """Test response includes Cache-Control: no-store header."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    token = resp.json()["wait_for_index_token"]
    
    # Check status endpoint
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.slow
async def test_full_cycle(wired_app, engine) -> None:
    """Test full cycle: index → poll until done (simulated)."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    data = resp.json()
    token = data["wait_for_index_token"]
    doc_id = uuid.UUID(token)
    
    # Poll until done (simulate by marking as done)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE search_outbox "
                "SET status = 'done', updated_at = now() "
                "WHERE document_id = :doc_id"
            ),
            {"doc_id": doc_id},
        )
    
    # Final poll should show done
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"


@pytest.mark.slow
async def test_status_in_progress_normalizes_to_pending(wired_app, engine) -> None:
    """Test that 'in_progress' status normalizes to 'pending' for client."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    token = resp.json()["wait_for_index_token"]
    doc_id = uuid.UUID(token)
    
    # Manually mark the outbox as in_progress
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE search_outbox "
                "SET status = 'in_progress', updated_at = now() "
                "WHERE document_id = :doc_id"
            ),
            {"doc_id": doc_id},
        )
    
    # Check status endpoint - should normalize to 'pending'
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"


@pytest.mark.slow
async def test_status_failed_normalizes_to_pending(wired_app, engine) -> None:
    """Test that 'failed' status normalizes to 'pending' for client."""
    wrapper, client = wired_app
    body = _payload()
    
    # Index a document
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    token = resp.json()["wait_for_index_token"]
    doc_id = uuid.UUID(token)
    
    # Manually mark the outbox as failed
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE search_outbox "
                "SET status = 'failed', updated_at = now() "
                "WHERE document_id = :doc_id"
            ),
            {"doc_id": doc_id},
        )
    
    # Check status endpoint - should normalize to 'pending'
    resp = await client.get(f"/index/status/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"