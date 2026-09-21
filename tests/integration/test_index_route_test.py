"""P-07: POST /index — transactional outbox + Redis Streams + fast-path.

Covers the ARCHITECT §4.2 dual-write, §4.3 idempotency/fast-path and the
Redis-unavailable safety net (reconciler catches up later).
"""

import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.db.redis_client import get_redis_client

STREAM = "embeddings.queue"


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


def _content_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


@pytest.mark.slow  # spins PG + Redis testcontainers
async def test_index_creates_document_and_outbox(wired_app, engine) -> None:
    wrapper, client = wired_app
    body = _payload()
    resp = await client.post("/index", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "queued"
    doc_id = uuid.UUID(data["doc_id"])
    assert data["wait_for_index_token"] == str(doc_id)
    assert data["indexed_at"]

    async with engine.begin() as conn:
        doc_count = await conn.scalar(
            text("SELECT count(*) FROM documents WHERE id = :id"), {"id": doc_id}
        )
        assert doc_count == 1
        outbox = (
            await conn.execute(
                text(
                    "SELECT op, status, content_hash FROM search_outbox "
                    "WHERE document_id = :id"
                ),
                {"id": doc_id},
            )
        ).all()
    assert len(outbox) == 1
    op, status, content_hash = outbox[0]
    assert op == "upsert"
    assert status == "pending"
    assert content_hash == _content_hash(body["title"], body["content"])


@pytest.mark.slow
async def test_index_publishes_to_redis_stream(wired_app, async_redis) -> None:
    _, client = wired_app
    resp = await client.post("/index", json=_payload(external_ref="stream-1"))
    assert resp.status_code == 201
    length = await async_redis.xlen(STREAM)
    assert length == 1
    entries = await async_redis.xrange(STREAM)
    doc_id, op = entries[0][1][b"doc_id"], entries[0][1][b"op"]
    assert uuid.UUID(doc_id.decode()) == uuid.UUID(resp.json()["doc_id"])
    assert op == b"upsert"


@pytest.mark.slow
async def test_reindex_identical_content_is_no_change(wired_app, engine) -> None:
    _, client = wired_app
    body = _payload(external_ref="dup-1", content="Same content")
    first = await client.post("/index", json=body)
    assert first.status_code == 201
    first_doc_id = first.json()["doc_id"]

    second = await client.post("/index", json=body)
    assert second.status_code == 201
    second_data = second.json()
    assert second_data["doc_id"] == first_doc_id
    assert second_data["status"] == "no_change"

    async with engine.begin() as conn:
        outbox_count = await conn.scalar(
            text("SELECT count(*) FROM search_outbox WHERE document_id = :id"),
            {"id": uuid.UUID(first_doc_id)},
        )
        doc_count = await conn.scalar(text("SELECT count(*) FROM documents"))
    assert outbox_count == 1  # fast-path: no new outbox row
    assert doc_count == 1


@pytest.mark.slow
async def test_reindex_changed_content_updates_document(wired_app, engine) -> None:
    _, client = wired_app
    body = _payload(external_ref="update-1", content="First version")
    first = await client.post("/index", json=body)
    assert first.status_code == 201
    doc_id = first.json()["doc_id"]

    updated = dict(body, content="Second version")
    second = await client.post("/index", json=updated)
    assert second.status_code == 201
    assert second.json()["doc_id"] == doc_id
    assert second.json()["status"] == "queued"

    async with engine.begin() as conn:
        content = await conn.scalar(
            text("SELECT content FROM documents WHERE id = :id"), {"id": uuid.UUID(doc_id)}
        )
        outbox_count = await conn.scalar(
            text("SELECT count(*) FROM search_outbox WHERE document_id = :id"),
            {"id": uuid.UUID(doc_id)},
        )
        hash_row = await conn.scalar(
            text("SELECT content_hash FROM documents WHERE id = :id"),
            {"id": uuid.UUID(doc_id)},
        )
    assert content == "Second version"
    assert outbox_count == 2  # a new upsert task for the changed content
    assert hash_row == _content_hash(updated["title"], updated["content"])


@pytest.mark.slow
async def test_index_succeeds_when_redis_unavailable(wired_app, engine) -> None:
    wrapper, client = wired_app

    class BrokenRedis:
        """Mimics a dead Redis: XADD raises, but the write path must survive."""

        async def xadd(self, *args: object, **kwargs: object) -> object:
            raise ConnectionError("redis is down")

    wrapper.dependency_overrides[get_redis_client] = BrokenRedis

    resp = await client.post("/index", json=_payload(external_ref="no-redis-1"))
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"

    async with engine.begin() as conn:
        outbox_count = await conn.scalar(text("SELECT count(*) FROM search_outbox"))
    assert outbox_count == 1  # reconciler (P-13) will pick this up


@pytest.mark.slow
async def test_language_auto_detection(wired_app, engine) -> None:
    _, client = wired_app
    resp = await client.post(
        "/index",
        json=_payload(
            external_ref="lang-1",
            title="Привет мир",
            content="Как дела сегодня на земле?",
        ),
    )
    assert resp.status_code == 201
    async with engine.begin() as conn:
        language = await conn.scalar(
            text("SELECT language FROM documents WHERE external_ref = 'lang-1'")
        )
    assert language == "ru"


@pytest.mark.slow
async def test_explicit_language_is_respected(wired_app, engine) -> None:
    _, client = wired_app
    resp = await client.post(
        "/index",
        json=_payload(external_ref="lang-2", content="Hello", language="fr"),
    )
    assert resp.status_code == 201
    async with engine.begin() as conn:
        language = await conn.scalar(
            text("SELECT language FROM documents WHERE external_ref = 'lang-2'")
        )
    assert language == "fr"


async def test_invalid_body_returns_422(wired_app) -> None:
    _, client = wired_app
    resp = await client.post("/index", json={"tenant_id": "not-a-uuid"})
    assert resp.status_code == 422
