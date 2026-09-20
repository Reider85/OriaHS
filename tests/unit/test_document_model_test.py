"""P-01: Document model round-trip against a real Postgres."""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.models import Document


@pytest.mark.slow  # spins a Postgres testcontainer
async def test_document_roundtrip(session) -> None:
    title, content = "Hello world", "This is a test document content"
    content_hash = hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()

    doc = Document(
        tenant_id=uuid4(),
        external_ref="ext-1",
        title=title,
        content=content,
        language="en",
        tags=["news", "sports"],
        attributes={"category": "AI"},
        embedding_model="bge-m3-v1",
        content_hash=content_hash,
    )
    session.add(doc)
    await session.flush()

    assert doc.id is not None

    stored = await session.get(Document, doc.id)
    assert stored is not None
    assert stored.content_hash == content_hash
    assert stored.tags == ["news", "sports"]
    assert stored.attributes == {"category": "AI"}
    assert stored.embedding_model == "bge-m3-v1"
    assert stored.deleted_at is None

    # tsv is a stored generated column — populated by the DB, not by the app
    tsv = await session.scalar(text("SELECT tsv FROM documents WHERE id = :id"), {"id": doc.id})
    assert tsv is not None
    assert "hello" in str(tsv)
