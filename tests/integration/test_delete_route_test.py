"""P-08: DELETE /index/{doc_id} — soft-delete + outbox ``op='delete'``.

Covers the ARCHITECT §14.4 endpoint: 204 on success and on idempotent
re-delete, 404 for a never-existing document, 422 for an unparseable UUID,
and the Redis Streams /unavailable-Redis safety net.
"""

import uuid

import pytest
from sqlalchemy import text

from app.db.redis_client import get_redis_client

STREAM = "embeddings.queue"


def _payload(external_ref: str = "del-1") -> dict:
    return {
        "tenant_id": str(uuid.uuid4()),
        "external_ref": external_ref,
        "title": "Hello",
        "content": "Hello world",
        "tags": ["greeting"],
        "attributes": {"category": "test"},
    }


async def _index_doc(client, external_ref: str = "del-1") -> uuid.UUID:
    resp = await client.post("/index", json=_payload(external_ref))
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["doc_id"])


@pytest.mark.slow  # spins PG + Redis testcontainers
async def test_delete_soft_deletes_document_and_outbox(wired_app, engine) -> None:
    _, client = wired_app
    doc_id = await _index_doc(client)

    resp = await client.delete(f"/index/{doc_id}")
    assert resp.status_code == 204
    assert resp.content == b""  # 204 must not carry a body (HTTP spec)

    async with engine.begin() as conn:
        deleted_at = await conn.scalar(
            text("SELECT deleted_at FROM documents WHERE id = :id"), {"id": doc_id}
        )
        outbox = (
            await conn.execute(
                text("SELECT op, status, content_hash FROM search_outbox WHERE document_id = :id"),
                {"id": doc_id},
            )
        ).all()
    assert deleted_at is not None
    assert len(outbox) == 2  # index upsert + delete
    delete_row = [row for row in outbox if row[0] == "delete"]
    assert len(delete_row) == 1
    op, status, content_hash = delete_row[0]
    assert op == "delete"
    assert status == "pending"
    assert content_hash is None


@pytest.mark.slow
async def test_delete_is_idempotent(wired_app, engine) -> None:
    _, client = wired_app
    doc_id = await _index_doc(client)

    first = await client.delete(f"/index/{doc_id}")
    assert first.status_code == 204

    second = await client.delete(f"/index/{doc_id}")
    assert second.status_code == 204

    async with engine.begin() as conn:
        outbox_count = await conn.scalar(
            text("SELECT count(*) FROM search_outbox WHERE document_id = :id"),
            {"id": doc_id},
        )
    assert outbox_count == 2  # repeated DELETE must not add another row


@pytest.mark.slow
async def test_delete_nonexistent_returns_404(wired_app) -> None:
    _, client = wired_app
    resp = await client.delete(f"/index/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Document not found"}


@pytest.mark.slow
async def test_delete_invalid_uuid_returns_422(wired_app) -> None:
    _, client = wired_app
    resp = await client.delete("/index/not-a-uuid")
    assert resp.status_code == 422


@pytest.mark.slow
async def test_delete_publishes_to_redis_stream(wired_app, async_redis) -> None:
    _, client = wired_app
    doc_id = await _index_doc(client, external_ref="del-stream-1")

    resp = await client.delete(f"/index/{doc_id}")
    assert resp.status_code == 204

    # Stream is append-only: index upsert entry followed by the delete entry.
    last_id, fields = (await async_redis.xrevrange(STREAM, count=1))[0]
    assert uuid.UUID(fields[b"doc_id"].decode()) == doc_id
    assert fields[b"op"] == b"delete"

    # Idempotent second DELETE -> the stream must not grow.
    before = await async_redis.xlen(STREAM)
    repeat = await client.delete(f"/index/{doc_id}")
    assert repeat.status_code == 204
    assert await async_redis.xlen(STREAM) == before


@pytest.mark.slow
async def test_delete_succeeds_when_redis_unavailable(wired_app, engine) -> None:
    wrapper, client = wired_app

    class BrokenRedis:
        """Mimics a dead Redis: XADD raises, but the delete path must survive."""

        async def xadd(self, *args: object, **kwargs: object) -> object:
            raise ConnectionError("redis is down")

    wrapper.dependency_overrides[get_redis_client] = BrokenRedis

    doc_id = await _index_doc(client, external_ref="del-no-redis-1")
    resp = await client.delete(f"/index/{doc_id}")
    assert resp.status_code == 204

    async with engine.begin() as conn:
        outbox_count = await conn.scalar(
            text("SELECT count(*) FROM search_outbox WHERE document_id = :id"),
            {"id": doc_id},
        )
        deleted_at = await conn.scalar(
            text("SELECT deleted_at FROM documents WHERE id = :id"), {"id": doc_id}
        )
    assert outbox_count == 2  # upsert (from index) + delete
    assert deleted_at is not None
