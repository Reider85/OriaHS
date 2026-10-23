"""Qdrant collection management for blue-green deployments (PR-07).

Provides collection creation, configuration loading, and validation helpers.
"""

from pathlib import Path
from typing import Any

import structlog
import yaml
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

logger = structlog.get_logger("collection")


def load_collection_spec() -> dict[str, Any]:
    """Load collection specification from qdrant_collections.yaml.
    
    Returns:
        Dictionary containing collection configuration
    """
    spec_path = Path("qdrant_collections.yaml")

    if not spec_path.exists():
        raise FileNotFoundError(f"Collection spec not found: {spec_path}")

    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    return spec


def get_collection_config(collection_name: str) -> dict[str, Any]:
    """Get configuration for a specific collection from the spec.
    
    Args:
        collection_name: Name of the collection
    
    Returns:
        Collection configuration dictionary
    """
    spec = load_collection_spec()

    if collection_name not in spec.get("collections", {}):
        raise ValueError(f"Collection {collection_name} not found in spec")

    return spec["collections"][collection_name]


async def ensure_collection_exists(
    client: QdrantClient,
    collection_name: str,
    vector_size: int,
    replication_factor: int = 1,
    config_overrides: dict[str, Any] | None = None,
) -> None:
    """Ensure Qdrant collection exists with correct configuration.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        vector_size: Dimension of vectors
        replication_factor: Replication factor for the collection
        config_overrides: Optional configuration overrides
    """
    # Check if collection exists
    if client.collection_exists(collection_name):
        # Validate configuration
        await _validate_collection_config(
            client,
            collection_name,
            vector_size,
            replication_factor,
            config_overrides,
        )
        return

    # Get base configuration from spec
    try:
        base_config = get_collection_config(collection_name)
    except ValueError:
        # Collection not in spec, use default config
        base_config = {}

    # Merge with overrides
    config = {
        "vectors": {
            "size": vector_size,
            "distance": "Cosine",
        },
        "replication_factor": replication_factor,
    }

    if config_overrides:
        config.update(config_overrides)

    # Apply base config (except size and distance which are specified)
    if "vectors" in base_config:
        vectors_config = base_config["vectors"]
        if "params" in vectors_config:
            config["vectors"].update(vectors_config["params"])

    # Create collection
    await client.create_collection(
        collection_name=collection_name,
        config=qdrant_models.CreateCollectionConfig(
            vectors=qdrant_models.VectorParams(
                size=vector_size,
                distance=qdrant_models.Distance.COSINE,
            ),
            replication_factor=replication_factor,
        ),
    )

    logger.info(
        "Collection created",
        collection=collection_name,
        vector_size=vector_size,
        replication_factor=replication_factor,
    )


async def _validate_collection_config(
    client: QdrantClient,
    collection_name: str,
    expected_vector_size: int,
    expected_replication_factor: int,
    config_overrides: dict[str, Any] | None = None,
) -> None:
    """Validate existing collection configuration.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        expected_vector_size: Expected vector dimension
        expected_replication_factor: Expected replication factor
        config_overrides: Optional configuration overrides
    
    Raises:
        ValueError: If configuration doesn't match expectations
    """
    # Get collection info
    collection_info = await client.get_collection(collection_name)

    # Check vector size
    actual_vector_size = collection_info.config.params.vectors.size
    if actual_vector_size != expected_vector_size:
        raise ValueError(
            f"Collection {collection_name} has vector size {actual_vector_size}, "
            f"expected {expected_vector_size}"
        )

    # Check replication factor
    actual_replication_factor = collection_info.config.replication_factor
    if actual_replication_factor != expected_replication_factor:
        raise ValueError(
            f"Collection {collection_name} has replication factor {actual_replication_factor}, "
            f"expected {expected_replication_factor}"
        )

    # Check distance (always cosine for our use case)
    actual_distance = collection_info.config.params.vectors.distance
    if actual_distance != qdrant_models.Distance.COSINE:
        raise ValueError(
            f"Collection {collection_name} has distance {actual_distance}, "
            f"expected cosine"
        )

    logger.debug(
        "Collection configuration validated",
        collection=collection_name,
        vector_size=actual_vector_size,
        replication_factor=actual_replication_factor,
    )


async def delete_collection(client: QdrantClient, collection_name: str) -> None:
    """Delete a Qdrant collection.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection to delete
    """
    try:
        await client.delete_collection(collection_name)
        logger.info("Collection deleted", collection=collection_name)
    except Exception as e:
        logger.warning(
            "Failed to delete collection",
            collection=collection_name,
            error=str(e),
        )
        raise


async def list_collections(client: QdrantClient) -> list[str]:
    """List all collections.
    
    Args:
        client: Qdrant client
    
    Returns:
        List of collection names
    """
    collections = await client.get_collections()
    return [collection.name for collection in collections.collections]


async def get_collection_info(client: QdrantClient, collection_name: str) -> dict[str, Any]:
    """Get collection information.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
    
    Returns:
        Dictionary with collection information
    """
    collection_info = await client.get_collection(collection_name)

    return {
        "name": collection_name,
        "vector_size": collection_info.config.params.vectors.size,
        "distance": collection_info.config.params.vectors.distance,
        "replication_factor": collection_info.config.replication_factor,
        "status": collection_info.status,
        "vectors_count": collection_info.vectors_count,
        "indexed_vectors_count": collection_info.indexed_vectors_count,
    }


# Re-export functions from scripts/init_qdrant for backward compatibility
# This allows existing tests to continue working without changes
def _diff_collection(
    client: QdrantClient,
    collection_name: str,
    config: dict[str, Any],
) -> bool:
    """Compare collection configuration with expected (from scripts/init_qdrant).
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        config: Expected configuration
    
    Returns:
        True if configurations match, False otherwise
    """
    try:
        collection_info = client.get_collection(collection_name)

        # Check vectors config
        expected_vectors = config.get("vectors", {})
        actual_vectors = collection_info.config.params.vectors

        if actual_vectors.size != expected_vectors.get("size", 1024):
            return False

        if actual_vectors.distance != expected_vectors.get("distance", "Cosine"):
            return False

        # Check replication factor
        expected_replication = config.get("replication_factor", 1)
        actual_replication = collection_info.config.replication_factor

        if actual_replication != expected_replication:
            return False

        return True

    except Exception:
        return False


def init_collection(
    client: QdrantClient,
    collection_name: str,
    config: dict[str, Any],
) -> None:
    """Initialize collection with configuration (from scripts/init_qdrant).
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        config: Collection configuration
    """
    # Use ensure_collection_exists with the provided config
    vector_size = config.get("vectors", {}).get("size", 1024)
    replication_factor = config.get("replication_factor", 1)

    asyncio.create_task(
        ensure_collection_exists(
            client,
            collection_name,
            vector_size,
            replication_factor,
            config,
        )
    )


__all__ = [
    "load_collection_spec",
    "get_collection_config",
    "ensure_collection_exists",
    "delete_collection",
    "list_collections",
    "get_collection_info",
    "_diff_collection",
    "init_collection",
]
