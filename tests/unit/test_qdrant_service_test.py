"""Unit tests for QdrantService.check_health (B-07)."""

from unittest.mock import AsyncMock, MagicMock

from app.services.qdrant import QdrantService


def _make_service(client: MagicMock) -> QdrantService:
    return QdrantService(client=client, collection_name="documents")


async def test_qdrant_check_health_returns_bool() -> None:
    """check_health returns True on success, False on exception."""
    client = MagicMock()
    client.get_collection = AsyncMock(return_value=MagicMock())
    service = _make_service(client)

    assert await service.check_health() is True

    client.get_collection = AsyncMock(side_effect=Exception("connection refused"))
    assert await service.check_health() is False

    client.get_collection = AsyncMock(side_effect=TimeoutError())
    assert await service.check_health() is False
