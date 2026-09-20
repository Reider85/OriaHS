"""Idempotent Qdrant collection initializer (ARCHITECT §17.1, P-04).

Creates the ``documents`` collection + payload indexes from
``qdrant_collections.yaml``. Re-runs are no-ops when the config matches and
print warnings on any drift (never overwrites — changing vectors/hnsw/etc.
requires a full reindex, see ARCHITECT §13).

Usage:
    python scripts/init_qdrant.py [--config qdrant_collections.yaml] [--url http://localhost:6333]
"""

import argparse
import os
import sys
from pathlib import Path

import yaml
from qdrant_client import QdrantClient
from qdrant_client.http import models as q

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search.qdrant_client import get_qdrant_client  # noqa: E402

DEFAULT_CONFIG = ROOT / "qdrant_collections.yaml"

_SCHEMA_TO_TYPE = {
    "keyword": q.PayloadSchemaType.KEYWORD,
    "keyword[]": q.PayloadSchemaType.KEYWORD,
    "float": q.PayloadSchemaType.FLOAT,
    "integer": q.PayloadSchemaType.INTEGER,
    "datetime": q.PayloadSchemaType.DATETIME,
}


def _load_specs(config_path: Path) -> list[dict]:
    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["collections"]


def _build_create_params(spec: dict) -> q.CreateCollection:
    scalar = spec["quantization_config"]["scalar"]
    return q.CreateCollection(
        vectors=q.VectorParams(
            size=spec["vectors"]["size"],
            distance=q.Distance.COSINE,
        ),
        shard_number=spec["shard_number"],
        replication_factor=spec["replication_factor"],
        on_disk_payload=spec["on_disk_payload"],
        hnsw_config=q.HnswConfigDiff(**spec["hnsw_config"]),
        optimizers_config=q.OptimizersConfigDiff(**spec["optimizers_config"]),
        quantization_config=q.ScalarQuantization(
            scalar=q.ScalarQuantizationConfig(
                type=q.ScalarType.INT8,
                quantile=scalar["quantile"],
                always_ram=scalar["always_ram"],
            )
        ),
    )


def _effective_replication(spec: dict) -> int:
    raw = os.environ.get("QDRANT_REPLICATION_FACTOR")
    if raw is not None:
        return int(raw)
    return int(spec["replication_factor"])


def _diff_collection(info: object, spec: dict) -> list[str]:
    """Return a list of meaningful drift messages ([] if none)."""
    problems: list[str] = []
    config = info.config  # type: ignore[attr-defined]
    params = config.params

    vectors = params.vectors
    size = getattr(vectors, "size", None)
    distance = getattr(getattr(vectors, "distance", None), "name", None)
    if size != spec["vectors"]["size"]:
        problems.append(f"vectors.size={size} != {spec['vectors']['size']}")
    if distance is not None and distance.upper() != spec["vectors"]["distance"].upper():
        problems.append(f"vectors.distance={distance} != {spec['vectors']['distance']}")
    if params.shard_number != spec["shard_number"]:
        problems.append(f"shard_number={params.shard_number} != {spec['shard_number']}")
    if params.on_disk_payload != spec["on_disk_payload"]:
        problems.append(f"on_disk_payload={params.on_disk_payload} != {spec['on_disk_payload']}")

    hnsw = config.hnsw_config
    for key, expected in spec["hnsw_config"].items():
        actual = getattr(hnsw, key, None)
        if actual is not None and actual != expected:
            problems.append(f"hnsw.{key}={actual} != {expected}")

    optimizers = config.optimizer_config
    for key, expected in spec["optimizers_config"].items():
        actual = getattr(optimizers, key, None)
        if actual is not None and actual != expected:
            problems.append(f"optimizers.{key}={actual} != {expected}")

    scalar = getattr(config.quantization_config, "scalar", None)
    if scalar is None or scalar.type != q.ScalarType.INT8:
        problems.append("quantization.scalar.type != int8")
    return problems


def _sync_payload_indexes(
    client: QdrantClient,
    collection: str,
    spec: dict,
) -> None:
    existing = client.get_collection(collection).payload_schema
    for entry in spec.get("payload_indexes", []):
        field, expected = entry["field"], _SCHEMA_TO_TYPE[entry["type"]]
        info = existing.get(field)
        if info is None:
            client.create_payload_index(collection_name=collection, field_name=field, field_schema=expected)
            print(f"  created payload index {field} ({entry['type']})")
        elif info.data_type != expected:
            print(
                f"  WARNING payload index {field}: type={info.data_type.name.lower()} "
                f"!= spec {entry['type']}"
            )


def init_collection(client: QdrantClient, spec: dict) -> bool:
    name = spec["name"]
    effective_replication = _effective_replication(spec)

    existing = client.collection_exists(name)
    if not existing:
        params = _build_create_params(spec)
        client.create_collection(
            collection_name=name,
            vectors_config=params.vectors,
            shard_number=params.shard_number,
            replication_factor=effective_replication,
            on_disk_payload=params.on_disk_payload,
            hnsw_config=params.hnsw_config,
            optimizers_config=params.optimizers_config,
            quantization_config=params.quantization_config,
        )
        print(f"created collection {name}")
        _sync_payload_indexes(client, name, spec)
        return True

    info = client.get_collection(name)
    problems = _diff_collection(info, spec)
    if problems:
        print(f"WARNING collection {name} exists with drift:")
        for p in problems:
            print(f"  - {p}")
        print("  Refusing to overwrite — a reindex is required (ARCHITECT §13).")
        return False

    _sync_payload_indexes(client, name, spec)
    print(f"collection {name} matches config (no-op)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--url", default=None, help="Qdrant HTTP URL (default: from settings/.env)")
    args = parser.parse_args()

    client: QdrantClient = (
        QdrantClient(url=args.url) if args.url else get_qdrant_client()
    )
    for spec in _load_specs(args.config):
        init_collection(client, spec)


if __name__ == "__main__":
    main()
