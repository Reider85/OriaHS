"""P-04: init_qdrant idempotency + config drift detection (mocked client)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from qdrant_client.http import models as q

from scripts.init_qdrant import _diff_collection, init_collection


def _spec() -> dict:
    return {
        "name": "documents",
        "vectors": {"size": 1024, "distance": "Cosine"},
        "shard_number": 4,
        "replication_factor": 2,
        "write_consistency": "majority",
        "on_disk_payload": True,
        "hnsw_config": {"m": 16, "ef_construct": 200, "full_scan_threshold": 10000},
        "optimizers_config": {"default_segment_number": 4, "indexing_threshold": 20000},
        "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
        "payload_indexes": [{"field": "tenant_id", "type": "keyword"}],
    }


def _collection_info() -> SimpleNamespace:
    """Lightweight stand-in for qdrant CollectionInfo (script reads only attrs)."""
    return SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=SimpleNamespace(size=1024, distance=SimpleNamespace(name="Cosine")),
                shard_number=4,
                on_disk_payload=True,
            ),
            hnsw_config=SimpleNamespace(m=16, ef_construct=200, full_scan_threshold=10000),
            optimizer_config=SimpleNamespace(default_segment_number=4, indexing_threshold=20000),
            quantization_config=SimpleNamespace(scalar=SimpleNamespace(type=q.ScalarType.INT8)),
        ),
        payload_schema={},
    )


def test_diff_no_drift() -> None:
    assert _diff_collection(_collection_info(), _spec()) == []


def test_diff_detects_hnsw_drift() -> None:
    spec = _spec()
    spec["hnsw_config"]["m"] = 32
    problems = _diff_collection(_collection_info(), spec)
    assert any("hnsw.m" in p for p in problems)


def test_diff_detects_distance_drift() -> None:
    spec = _spec()
    spec["vectors"]["distance"] = "Euclid"
    problems = _diff_collection(_collection_info(), spec)
    assert any("vectors.distance" in p for p in problems)


def test_creates_when_absent() -> None:
    client = MagicMock()
    client.collection_exists.return_value = False
    client.get_collection.return_value = _collection_info()

    assert init_collection(client, _spec()) is True
    client.create_collection.assert_called_once()
    client.create_payload_index.assert_any_call(
        collection_name="documents",
        field_name="tenant_id",
        field_schema=q.PayloadSchemaType.KEYWORD,
    )


def test_noop_when_matches() -> None:
    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value = _collection_info()

    assert init_collection(client, _spec()) is True
    client.create_collection.assert_not_called()


def test_warns_and_skips_on_drift() -> None:
    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value = _collection_info()

    spec = _spec()
    spec["vectors"]["size"] = 768
    assert init_collection(client, spec) is False
    client.create_collection.assert_not_called()
