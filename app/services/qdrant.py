"""Qdrant service layer for the reconciler (ARCHITECT §4.4, P-13).

Thin async wrapper over ``AsyncQdrantClient`` used by the outbox worker:
point upsert, delete and the ``content_hash`` fast-path read (ARCHITECT
§4.3, ROADMAP §3.3). All payloads are built from ``QdrantPayload`` (P-04)
to prevent schema drift between Postgres and Qdrant.
"""

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import settings
from app.search.qdrant_client import get_async_qdrant_client


class QdrantService:
    """Async Qdrant operations bound to the ``documents`` collection."""

    def __init__(
        self,
        client: AsyncQdrantClient | None = None,
        collection_name: str | None = None,
    ) -> None:
        self._client = client or get_async_qdrant_client()
        self._collection = collection_name or settings.qdrant.collection

    def get_async_client(self) -> AsyncQdrantClient:
        """Get the async Qdrant client."""
        return self._client

    async def get_point_content_hash(self, doc_id: UUID) -> str | None:
        """Fast-path read: the stored ``content_hash`` of a point, if any.

        Returns ``None`` when the point does not exist or carries no hash.
        """
        points = await self._client.retrieve(
            collection_name=self._collection,
            ids=[str(doc_id)],
            with_payload=True,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        value = payload.get("content_hash")
        return value if isinstance(value, str) else None

    async def upsert_point(
        self, doc_id: UUID, vector: Sequence[float], payload: dict[str, Any]
    ) -> None:
        """Upsert one vector + payload point (idempotent by ``doc_id``)."""
        point = qmodels.PointStruct(
            id=str(doc_id),
            vector=list(vector),
            payload=payload,
        )
        await self._client.upsert(
            collection_name=self._collection,
            points=[point],
        )

    async def delete_point(self, doc_id: UUID) -> None:
        """Remove the point for ``doc_id`` (idempotent — missing point is OK)."""
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qmodels.PointIdsList(points=[str(doc_id)]),
        )

    async def check_health(self) -> bool:
        """Check Qdrant connectivity and collection availability.

        Returns True if Qdrant is responsive and collection exists, False otherwise.
        Uses 100ms timeout to avoid blocking search requests.
        """
        try:
            await asyncio.wait_for(self._client.get_collection_info(self._collection), timeout=0.1)
            return True
        except Exception:
            return False


__all__ = ["QdrantService"]
