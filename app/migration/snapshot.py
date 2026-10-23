"""Qdrant snapshot management for migration rollback (PR-07).

Handles snapshot creation, S3 upload, and restoration.
"""

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Any

import structlog
from qdrant_client import QdrantClient

from app.config import settings
from app.observability.metrics import migration_snapshots_created_total

if TYPE_CHECKING:
    from botocore.client import BaseClient

logger = structlog.get_logger("snapshot")


class SnapshotManager:
    """Manager for Qdrant snapshot operations."""

    def __init__(self, qdrant_client: QdrantClient, s3_client: "BaseClient | None" = None):
        self.qdrant_client = qdrant_client
        self.s3_client = s3_client

    async def create_and_upload_snapshot(
        self,
        collection: str,
        s3_prefix: str = "snapshots",
    ) -> str:
        """Create Qdrant snapshot and upload to S3.
        
        Args:
            collection: Collection name to snapshot
            s3_prefix: S3 prefix for the snapshot (default: "snapshots")
        
        Returns:
            S3 key for the uploaded snapshot
        """
        # Create snapshot
        snapshot_info = await self.qdrant_client.create_snapshot(collection)

        # Download snapshot locally
        local_path = await self.qdrant_client.download_snapshot(
            snapshot_info.name,
            collection,
        )

        # Upload to S3
        s3_key = f"{s3_prefix}/{collection}/{snapshot_info.name}.snapshot"

        if self.s3_client:
            await self._upload_to_s3(local_path, s3_key)

        # Clean up local file
        try:
            os.unlink(local_path)
        except OSError as e:
            logger.warning(
                "Failed to delete local snapshot file",
                local_path=local_path,
                error=str(e),
            )

        # Update metrics
        migration_snapshots_created_total.inc(labels={"collection": collection})

        logger.info(
            "Snapshot created and uploaded",
            collection=collection,
            snapshot_name=snapshot_info.name,
            s3_key=s3_key,
            size_bytes=snapshot_info.size,
        )

        return s3_key

    async def restore_from_s3(
        self,
        collection: str,
        s3_key: str,
        timeout: int = 300,
    ) -> None:
        """Restore Qdrant collection from S3 snapshot.
        
        Args:
            collection: Collection to restore
            s3_key: S3 key of the snapshot
            timeout: Timeout for restore operation in seconds
        """
        # Download snapshot from S3
        local_path = await self._download_from_s3(s3_key)

        try:
            # Restore snapshot
            await self.qdrant_client.restore_snapshot(
                collection,
                local_path,
                timeout=timeout,
            )

            logger.info(
                "Snapshot restored",
                collection=collection,
                s3_key=s3_key,
            )

        finally:
            # Clean up local file
            try:
                os.unlink(local_path)
            except OSError as e:
                logger.warning(
                    "Failed to delete local snapshot file",
                    local_path=local_path,
                    error=str(e),
                )

    async def _upload_to_s3(self, local_path: str, s3_key: str) -> None:
        """Upload file to S3 using asyncio."""
        loop = asyncio.get_event_loop()

        await loop.run_in_executor(
            None,
            self.s3_client.upload_file,
            local_path,
            settings.migration.snapshot_s3_bucket,
            s3_key,
        )

    async def _download_from_s3(self, s3_key: str) -> str:
        """Download file from S3 using asyncio."""
        loop = asyncio.get_event_loop()

        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_path = tmp_file.name

        # Download
        await loop.run_in_executor(
            None,
            self.s3_client.download_file,
            settings.migration.snapshot_s3_bucket,
            s3_key,
            tmp_path,
        )

        return tmp_path

    async def list_snapshots(self, collection: str | None = None) -> list[dict[str, Any]]:
        """List available snapshots.
        
        Args:
            collection: Optional collection name filter
        
        Returns:
            List of snapshot information dictionaries
        """
        snapshots = await self.qdrant_client.list_snapshots()

        if collection:
            snapshots = [s for s in snapshots.snaphots if s.collection_name == collection]

        result = []
        for snapshot in snapshots.snapshots:
            result.append({
                "name": snapshot.name,
                "collection_name": snapshot.collection_name,
                "size_bytes": snapshot.size,
                "created_at": snapshot.created_at,
                "location": snapshot.location,
            })

        return result

    async def delete_snapshot(self, snapshot_name: str) -> None:
        """Delete a snapshot from Qdrant.
        
        Args:
            snapshot_name: Name of the snapshot to delete
        """
        await self.qdrant_client.delete_snapshot(snapshot_name)
        logger.info("Snapshot deleted", snapshot_name=snapshot_name)


async def create_snapshot_manager(
    qdrant_client: QdrantClient,
    s3_client: "BaseClient | None" = None,
) -> SnapshotManager:
    """Create a snapshot manager with the given clients."""
    return SnapshotManager(qdrant_client, s3_client)


__all__ = ["SnapshotManager", "create_snapshot_manager"]
