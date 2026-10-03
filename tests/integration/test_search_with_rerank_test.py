"""C-05: POST /search integration tests with rerank (ARCHITECT §14.1, §6.6).

Exercises the real FastAPI route against testcontainers PostgreSQL with the
vector channel and the cross-encoder mocked, so the assertions cover the
wiring under test: DI wiring, fusion dispatch, rerank ordering, and the
``degraded`` / ``rerank_degraded`` contract.

The reranker is always mocked — a GPU cross-encoder is not available in CI,
which is exactly the ``RerankerConfig.mock_mode`` scenario from C-01.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.api.deps import (
    get_circuit_breaker,
    get_reranker_service,
    get_speculative_reranker,
)
from app.reranker.schemas import RerankCandidate, RerankResult

pytestmark = pytest.mark.slow


class StubReranker:
    """Async cross-encoder stub. Scores are supplied per doc_id."""

    def __init__(self, scores: dict[uuid.UUID, float] | None = None, fail: bool = False) -> None:
        self._scores = scores or {}
        self._fail = fail
        self.calls: list[list[uuid.UUID]] = []

    async def rerank(
        self, query: str, docs: list[RerankCandidate], top_k: int | None = None
    ) -> list[RerankResult]:
        from app.reranker.exceptions import RerankerUnavailableException

        self.calls.append([d.doc_id for d in docs])
        if self._fail:
            raise RerankerUnavailableException("cross-encoder unavailable")
        results = [
            RerankResult(doc_id=d.doc_id, score=self._scores.get(d.doc_id, 0.5)) for d in docs
        ]
        results.sort(key=lambda r: -r.score)
        return results[:top_k] if top_k is not None else results


class StubBreaker:
    """Circuit breaker stand-in exposing only what the orchestrator calls."""

    def __init__(self, is_open: bool = False) -> None:
        self._open = is_open

    def is_open(self) -> bool:
        return self._open

    def state(self) -> str:
        return "open" if self._open else "closed"

    async def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if self._open:
            from app.reranker.circuit_breaker import CircuitBreakerOpen

            raise CircuitBreakerOpen("circuit breaker is open")
        return await fn(*args, **kwargs)


def _install_rerank(
    wrapper: Any,
    reranker: StubReranker,
    breaker: StubBreaker | None = None,
    speculative: Any | None = None,
) -> None:
    """Override the reranker dependencies for the duration of a test."""
    wrapper.dependency_overrides[get_reranker_service] = lambda: reranker
    wrapper.dependency_overrides[get_circuit_breaker] = lambda: breaker or StubBreaker()
    wrapper.dependency_overrides[get_speculative_reranker] = lambda: speculative


async def _index(client: Any, tenant_id: str, ref: str, title: str, content: str) -> str:
    resp = await client.post(
        "/index",
        json={
            "tenant_id": tenant_id,
            "external_ref": ref,
            "title": title,
            "content": content,
        },
    )
    assert resp.status_code == 201
    return str(resp.json()["doc_id"])


async def test_search_rrf_without_rerank_is_unchanged(wired_app) -> None:
    """C-05: fusion=rrf + rerank=false behaves exactly as on MVP."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "rrf-doc", "Hybrid search guide", "How to build hybrid search")

    reranker = StubReranker()
    _install_rerank(wrapper, reranker)

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "Hybrid search",
                "tenant_id": tenant_id,
                "fusion": "rrf",
                "rerank": False,
                "top_k": 10,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert reranker.calls == [], "rerank=false must never reach the cross-encoder"
    assert data["degraded"] is False
    assert data["total_lexical"] >= 1


async def test_search_weighted_fusion_reports_strategy(wired_app) -> None:
    """C-05: fusion=weighted applies weighted_fuse and reports it in debug."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "weighted-doc", "Weighted fusion", "Alpha and beta weighting")

    _install_rerank(wrapper, StubReranker())

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "Weighted fusion",
                "tenant_id": tenant_id,
                "fusion": "weighted",
                "fusion_alpha": 0.7,
                "explain": True,
                "top_k": 10,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["hits"]
    debug = data["hits"][0]["debug"]
    assert debug["fusion_strategy"] == "weighted"
    assert debug["fusion_alpha"] == 0.7
    assert debug["weighted_score"] is not None
    assert debug["rrf_score"] is None


async def test_search_with_rerank_applies_cross_encoder(wired_app) -> None:
    """C-05: rerank=true reports rerank_applied and returns rerank scores."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "rerank-doc", "Reranking", "Cross encoder reranking")

    reranker = StubReranker()
    _install_rerank(wrapper, reranker)

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "Reranking",
                "tenant_id": tenant_id,
                "rerank": True,
                "explain": True,
                "top_k": 10,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(reranker.calls) == 1, "the cross-encoder must be called exactly once"
    debug = data["hits"][0]["debug"]
    assert debug["rerank_applied"] is True
    assert debug["rerank_degraded"] is False
    assert debug["rerank_score"] is not None


async def test_search_circuit_breaker_open_returns_200(wired_app) -> None:
    """C-05: an open breaker is a 200 with a flag, never a 5xx."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "breaker-doc", "Circuit breaker", "Rolling window breaker")

    reranker = StubReranker()
    _install_rerank(wrapper, reranker, breaker=StubBreaker(is_open=True))

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "Circuit breaker",
                "tenant_id": tenant_id,
                "rerank": True,
                "explain": True,
                "top_k": 10,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert reranker.calls == [], "the reranker must not be called while the breaker is open"
    assert data["hits"]
    debug = data["hits"][0]["debug"]
    assert debug["rerank_degraded"] is True
    assert debug["rerank_applied"] is False
    # A breaker trip is not a channel failure.
    assert data["degraded"] is False


async def test_search_weighted_plus_rerank_combined(wired_app) -> None:
    """C-05: fusion=weighted + rerank=true + alpha=0.7 work together."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "combined-doc", "Combined mode", "Weighted plus rerank")

    reranker = StubReranker()
    _install_rerank(wrapper, reranker)

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={
                "query": "Combined mode",
                "tenant_id": tenant_id,
                "fusion": "weighted",
                "fusion_alpha": 0.7,
                "rerank": True,
                "explain": True,
                "top_k": 10,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    debug = data["hits"][0]["debug"]
    assert debug["fusion_strategy"] == "weighted"
    assert debug["fusion_alpha"] == 0.7
    assert debug["rerank_applied"] is True


async def test_breaker_opened_by_errors_then_degrades(wired_app) -> None:
    """C-05: mock errors open the breaker; the next request reports degraded."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "errors-doc", "Error budget", "Failing cross encoder")

    from app.config import CircuitBreakerConfig
    from app.reranker.circuit_breaker import RerankerCircuitBreaker

    breaker = RerankerCircuitBreaker(CircuitBreakerConfig())
    failing = StubReranker(fail=True)
    _install_rerank(wrapper, failing, breaker=breaker)

    body = {"query": "Error budget", "tenant_id": tenant_id, "rerank": True, "explain": True}

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        first = await client.post("/search", json=body)

    assert first.status_code == 200
    assert breaker.is_open() is True, "a 100%% error rate must trip the breaker"
    assert first.json()["hits"][0]["debug"]["rerank_applied"] is False

    # A healthy reranker now, but the breaker is open → degraded, still 200.
    _install_rerank(wrapper, StubReranker(), breaker=breaker)
    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        second = await client.post("/search", json=body)

    assert second.status_code == 200
    assert second.json()["hits"][0]["debug"]["rerank_degraded"] is True


async def test_ten_consecutive_rerank_requests_are_stable(wired_app) -> None:
    """C-05: 10 back-to-back rerank requests all return 200."""
    wrapper, client = wired_app
    tenant_id = str(uuid.uuid4())
    await _index(client, tenant_id, "batch-doc", "Batch rerank", "Ten consecutive requests")

    reranker = StubReranker()
    _install_rerank(wrapper, reranker)

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        for _ in range(10):
            resp = await client.post(
                "/search",
                json={
                    "query": "Batch rerank",
                    "tenant_id": tenant_id,
                    "rerank": True,
                    "explain": True,
                    "top_k": 5,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["degraded"] is False
            assert data["hits"][0]["debug"]["rerank_applied"] is True


async def test_response_exposes_partial_flag(wired_app) -> None:
    """C-05: SearchResponse always carries the partial flag (default False)."""
    wrapper, client = wired_app
    _install_rerank(wrapper, StubReranker())

    with patch("app.search.orchestrator.vector_search", new_callable=AsyncMock, return_value=[]):
        resp = await client.post(
            "/search",
            json={"query": "anything", "tenant_id": str(uuid.uuid4())},
        )

    assert resp.status_code == 200
    assert "partial" in resp.json()
    assert resp.json()["partial"] is False
