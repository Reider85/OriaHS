"""Qdrant service layer for the reconciler (ARCHITECT §4.4, P-13).

Thin async wrapper over ``AsyncQdrantClient`` used by the outbox worker:
point upsert, delete and the ``content_hash`` fast-path read (ARCHITECT
§4.3, ROADMAP §3.3). All payloads are built from ``QdrantPayload`` (P-04)
to prevent schema drift between Postgres and Qdrant.

Extended for PR-07 to support multi-collection operations and dual-write.
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
    """Async Qdrant operations for single or multiple collections."""

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

    async def get_point_content_hash(
        self, doc_id: UUID, collection_name: str | None = None
    ) -> str | None:
        """Fast-path read: the stored ``content_hash`` of a point, if any.

        Returns ``None`` when the point does not exist or carries no hash.
        """
        collection = collection_name or self._collection
        points = await self._client.retrieve(
            collection_name=collection,
            ids=[str(doc_id)],
            with_payload=True,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        value = payload.get("content_hash")
        return value if isinstance(value, str) else None

    async def upsert_point(
        self,
        doc_id: UUID,
        vector: Sequence[float],
        payload: dict[str, Any],
        collection_name: str | None = None,
    ) -> None:
        """Upsert one vector + payload point (idempotent by ``doc_id``).
        
        Args:
            doc_id: Document ID
            vector: Embedding vector
            payload: Point payload
            collection_name: Optional collection name (default: service's collection)
        """
        collection = collection_name or self._collection
        point = qmodels.PointStruct(
            id=str(doc_id),
            vector=list(vector),
            payload=payload,
        )
        await self._client.upsert(
            collection_name=collection,
            points=[point],
        )

    async def delete_point(
        self, doc_id: UUID, collection_name: str | None = None
    ) -> None:
        """Remove the point for ``doc_id`` (idempotent — missing point is OK).
        
        Args:
            doc_id: Document ID
            collection_name: Optional collection name (default: service's collection)
        """
        collection = collection_name or self._collection
        await self._client.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=[str(doc_id)]),
        )

    async def upsert_points(
        self,
        points: list[qmodels.PointStruct],
        collection_name: str | None = None,
    ) -> None:
        """Upsert multiple points to a collection.
        
        Args:
            points: List of PointStruct objects
            collection_name: Optional collection name (default: service's collection)
        """
        collection = collection_name or self._collection
        await self._client.upsert(
            collection_name=collection,
            points=points,
        )

    async def delete_collection(self, collection_name: str) -> None:
        """Delete a collection.
        
        Args:
            collection_name: Name of the collection to delete
        """
        await self._client.delete_collection(collection_name)

    async def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists.
        
        Args:
            collection_name: Name of the collection to check
        
        Returns:
            True if collection exists, False otherwise
        """
        try:
            await self._client.get_collection(collection_name)
            return True
        except Exception:
            return False

    async def get_collection_info(self, collection_name: str) -> dict[str, Any]:
        """Get collection information.
        
        Args:
            collection_name: Name of the collection
        
        Returns:
            Dictionary with collection information
        """
        collection_info = await self._client.get_collection(collection_name)
        return {
            "name": collection_name,
            "status": collection_info.status,
            "vectors_count": collection_info.vectors_count,
            "indexed_vectors_count": collection_info.indexed_vectors_count,
            "config": {
                "vector_size": collection_info.config.params.vectors.size,
                "distance": collection_info.config.params.vectors.distance,
                "replication_factor": collection_info.config.replication_factor,
            }
        }

    async def retrieve_points(
        self,
        doc_ids: list[UUID],
        collection_name: str | None = None,
        with_vector: bool = False,
        with_payload: bool = True,
    ) -> list[qmodels.PointStruct]:
        """Retrieve points by document IDs.
        
        Args:
            doc_ids: List of document IDs
            collection_name: Optional collection name (default: service's collection)
            with_vector: Whether to include vectors in response
            with_payload: Whether to include payload in response
        
        Returns:
            List of PointStruct objects
        """
        collection = collection_name or self._collection
        return await self._client.retrieve(
            collection_name=collection,
            ids=[str(doc_id) for doc_id in doc_ids],
            with_vector=with_vector,
            with_payload=with_payload,
        )

    async def check_health(self) -> bool:
        """Check Qdrant connectivity and collection availability.

        Returns True if Qdrant is responsive and collection exists, False otherwise.
        Uses 100ms timeout to avoid blocking search requests.
        """
        try:
            await asyncio.wait_for(self._client.get_collection(self._collection), timeout=0.1)
            return True
        except Exception:
            return False

    async def create_snapshot(
        self, collection_name: str, name: str | None = None
    ) -> qmodels.SnapshotDescription:
        """Create a snapshot of a collection.
        
        Args:
            collection_name: Name of the collection to snapshot
            name: Optional snapshot name (default: auto-generated)
        
        Returns:
            Snapshot description
        """
        return await self._client.create_snapshot(collection_name, name=name)

    async def download_snapshot(
        self, snapshot_name: str, collection_name: str, location: str | None = None
    ) -> str:
        """Download a snapshot to local file.
        
        Args:
            snapshot_name: Name of the snapshot
            collection_name: Name of the collection
            location: Optional download location (default: temp file)
        
        Returns:
            Path to the downloaded snapshot file
        """
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_path = tmp_file.name

        await self._client.download_snapshot(
            snapshot_name=snapshot_name,
            collection_name=collection_name,
            location=tmp_path,
        )

        return tmp_path

    async def restore_snapshot(
        self,
        collection_name: str,
        snapshot_path: str,
        timeout: int = 300,
    ) -> None:
        """Restore a collection from a snapshot.
        
        Args:
            collection_name: Name of the collection to restore
            snapshot_path: Path to the snapshot file
            timeout: Timeout for restore operation in seconds
        """
        await self._client.restore_snapshot(
            collection_name=collection_name,
            snapshot_path=snapshot_path,
            timeout=timeout,
        )


__all__ = ["QdrantService"]
