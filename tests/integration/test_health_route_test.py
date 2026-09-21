"""P-12: ``GET /health/live``, ``GET /health/ready`` and ``GET /metrics``.

Runs against testcontainers PostgreSQL + Redis (via ``wired_app``); Qdrant is
stubbed at the dependency boundary so both reachable and unreachable states
are exercised without a third container. Outbox-backlog grading is validated
by seeding pending rows directly.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import Document, OutboxItem
from app.search.qdrant_client import get_async_qdrant_client


class _FakeQdrantOk:
    """Minimal stand-in for ``AsyncQdrantClient`` that answers collection info."""

    async def get_collection(self, collection_name: str) -> dict[str, Any]:
        return {"status": "green", "config": {"params": {"vectors": {"size": 1024}}}}


class _FakeQdrantDown:
    """Stand-in that behaves like an unreachable Qdrant."""

    async def get_collection(self, collection_name: str) -> dict[str, Any]:
        raise ConnectionError("qdrant connection refused")


def _use_qdrant(wrapper: Any, fake: Any) -> None:
    wrapper.dependency_overrides[get_async_qdrant_client] = lambda: fake


async def _seed_pending_outbox(engine: AsyncEngine, count: int) -> None:
    """Insert ``count`` documents each with one pending outbox row."""
    docs = [
        {
            "id": uuid.uuid4(),
            "tenant_id": uuid.uuid4(),
            "external_ref": f"health-{i}",
            "title": "",
            "content": "",
            "language": "en",
            "tags": [],
            "attributes": {},
            "embedding_model": "bge-m3-v1",
            "embedding_rev": 1,
            "content_hash": f"hash-{i}",
        }
        for i in range(count)
    ]
    outbox = [{"document_id": doc["id"], "op": "upsert", "status": "pending"} for doc in docs]
    async with engine.begin() as conn:
        await conn.execute(insert(Document), docs)
        await conn.execute(insert(OutboxItem), outbox)


@pytest.mark.slow
async def test_health_live_always_ok(wired_app) -> None:
    _, client = wired_app
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


@pytest.mark.slow
async def test_health_ready_all_checks_ok(wired_app) -> None:
    wrapper, client = wired_app
    _use_qdrant(wrapper, _FakeQdrantOk())

    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"]["status"] == "ok"
    assert body["checks"]["qdrant"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"
    assert body["checks"]["outbox_lag"]["status"] == "ok"


@pytest.mark.slow
async def test_health_ready_qdrant_down(wired_app) -> None:
    wrapper, client = wired_app
    _use_qdrant(wrapper, _FakeQdrantDown())

    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["qdrant"]["status"] == "fail"
    # A single failed dependency must not hide the healthy ones.
    assert body["checks"]["postgres"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"


@pytest.mark.slow
async def test_health_ready_outbox_lag_degraded(wired_app, engine: AsyncEngine) -> None:
    wrapper, client = wired_app
    _use_qdrant(wrapper, _FakeQdrantOk())
    await _seed_pending_outbox(engine, 5000)

    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["outbox_lag"]["status"] == "degraded"


@pytest.mark.slow
async def test_health_ready_outbox_lag_fail(wired_app, engine: AsyncEngine) -> None:
    wrapper, client = wired_app
    _use_qdrant(wrapper, _FakeQdrantOk())
    await _seed_pending_outbox(engine, 15000)

    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["outbox_lag"]["status"] == "fail"


@pytest.mark.slow
async def test_metrics_endpoint_exposes_prometheus_format(wired_app) -> None:
    _, client = wired_app
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    for metric in ("http_requests_total", "http_request_duration_seconds", "index_lag_seconds"):
        assert metric in resp.text
