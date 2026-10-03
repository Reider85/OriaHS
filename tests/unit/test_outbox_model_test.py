"""P-02: OutboxItem model round-trip, defaults, CHECK constraints against real Postgres."""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Document, OutboxItem


@pytest.fixture
async def doc(session) -> Document:
    title, content = "t", "c"
    content_hash = hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()
    d = Document(
        tenant_id=uuid4(),
        external_ref="ext-1",
        title=title,
        content=content,
        language="en",
        embedding_model="bge-m3-v1",
        content_hash=content_hash,
    )
    session.add(d)
    await session.flush()
    return d


@pytest.mark.slow  # spins a Postgres testcontainer
async def test_outbox_defaults(session, doc) -> None:
    content_hash = hashlib.sha256(b"t\nc").hexdigest()

    item = OutboxItem(document_id=doc.id, op="upsert", content_hash=content_hash)
    session.add(item)
    await session.flush()

    assert item.id is not None
    assert item.status == "pending"
    assert item.attempts == 0
    assert item.next_retry_at is not None
    assert item.last_error is None
    assert item.created_at is not None

    stored = await session.get(OutboxItem, item.id)
    assert stored is not None
    assert stored.document_id == doc.id
    assert stored.op == "upsert"
    assert stored.content_hash == content_hash


@pytest.mark.slow
async def test_invalid_op_rejected(session, doc) -> None:
    session.add(OutboxItem(document_id=doc.id, op="invalid_op"))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.slow
async def test_invalid_status_rejected(session, doc) -> None:
    session.add(OutboxItem(document_id=doc.id, op="upsert", status="invalid_status"))
    with pytest.raises(IntegrityError):
        await session.flush()
