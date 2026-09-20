"""P-04: QdrantPayload pydantic model validation (ARCHITECT §3.2)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.search.qdrant_payload import QdrantPayload


def _payload(**overrides: object) -> QdrantPayload:
    base = {
        "doc_id": uuid4(),
        "tenant_id": uuid4(),
        "language": "ru",
        "tags": ["news", "tech"],
        "attributes": {"category": "AI", "price": 1500},
        "model_name": "bge-m3-v1",
        "model_rev": 1,
        "created_at": datetime.now(UTC),
        "content_hash": "abc123",
    }
    base.update(overrides)
    return QdrantPayload(**base)


def test_valid_payload() -> None:
    p = _payload()
    assert p.language == "ru"
    assert p.tags == ["news", "tech"]
    assert p.attributes == {"category": "AI", "price": 1500}
    assert p.model_name == "bge-m3-v1"
    assert p.model_rev == 1


def test_empty_collections() -> None:
    p = _payload(tags=[], attributes={})
    assert p.tags == []
    assert p.attributes == {}


def test_minimal_rev() -> None:
    p = _payload(model_rev=0)
    assert p.model_rev == 0


def test_created_at_serializes_as_iso() -> None:
    p = _payload(created_at=datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC))
    dumped = p.model_dump(mode="json")
    assert isinstance(dumped["created_at"], str)
    assert dumped["created_at"].startswith("2026-09-19T10:00:00")


def test_extra_field_forbidden() -> None:
    data = _payload().model_dump()
    data["extra_field"] = "x"
    with pytest.raises(ValidationError):
        QdrantPayload(**data)
