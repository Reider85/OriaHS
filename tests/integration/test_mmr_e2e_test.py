"""Integration tests for MMR diversification (PR-04).

Tests end-to-end MMR behavior over the ``POST /search`` route with mocked
channels and deterministic embedding vectors.
Uses the ``wired_app`` fixture (testcontainers PG + Redis).
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.schemas import SearchRequest
from app.config import settings
from app.search.lexical import LexicalHit
from app.search.vector import VectorHit

# Two near-duplicate documents plus one orthogonal one: pure relevance order
# and MMR order must differ.
VECTOR_A = [1.0, 0.0, 0.0]
VECTOR_A_DUP = [0.9, 0.1, 0.0]
VECTOR_B = [0.0, 1.0, 0.0]


def _make_hits() -> tuple[list[LexicalHit], list[VectorHit]]:
    """Build deterministic lexical + vector channel results.

    ``A`` and ``A duplicate`` are near-duplicates; ``B`` is orthogonal. The
    duplicate outranks ``B`` in the fused (RRF) order, so diversification has
    something to fix: it must promote ``B`` above the duplicate.
    """
    doc_a = LexicalHit(
        doc_id=uuid4(),
        score=0.9,
        title="Document A",
        content_snippet="Content A",
    )
    doc_a_dup = LexicalHit(
        doc_id=uuid4(),
        score=0.85,
        title="Document A duplicate",
        content_snippet="Content A duplicate",
    )
    doc_b = VectorHit(
        doc_id=uuid4(),
        score=0.6,
        title="Document B",
        content_snippet="Content B",
    )
    return [doc_a, doc_a_dup], [doc_a_dup, doc_b]


def _mock_embedding(lex_hits: list[LexicalHit], vec_hits: list[VectorHit]) -> AsyncMock:
    """Build an embedding service mock returning deterministic vectors.

    Vectors are keyed by the embedded text (the orchestrator embeds
    ``"{title}\\n{snippet}"``), so each document always gets the same vector
    regardless of the order in which documents are embedded.
    """
    by_title = {
        "Document A": VECTOR_A,
        "Document A duplicate": VECTOR_A_DUP,
        "Document B": VECTOR_B,
    }

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            title = text.split("\n", 1)[0]
            out.append(by_title.get(title, VECTOR_B))
        return out

    mock_service = AsyncMock()
    mock_service.embed_texts = AsyncMock(side_effect=embed_texts)
    return mock_service


@pytest.mark.integration
async def test_mmr_diversify_changes_order(wired_app):
    """Test MMR diversification reorders results vs. plain fusion."""
    app, client = wired_app
    lex_hits, vec_hits = _make_hits()

    with (
        patch("app.search.orchestrator.lexical_search", new=AsyncMock(return_value=lex_hits)),
        patch("app.search.orchestrator.vector_search", new=AsyncMock(return_value=vec_hits)),
        patch(
            "app.api.routes.search._get_embedding_service",
            new=lambda: _mock_embedding(lex_hits, vec_hits),
        ),
    ):
        plain = SearchRequest(
            query="test query",
            tenant_id=uuid4(),
            top_k=3,
            diversify=False,
        )
        response_plain = await client.post("/search", json=plain.model_dump(mode="json"))
        assert response_plain.status_code == 200
        order_plain = [hit["doc_id"] for hit in response_plain.json()["hits"]]

        diversified = SearchRequest(
            query="test query",
            tenant_id=uuid4(),
            top_k=3,
            diversify=True,
            explain=True,
        )
        response_div = await client.post("/search", json=diversified.model_dump(mode="json"))
        assert response_div.status_code == 200
        data = response_div.json()
        order_div = [hit["doc_id"] for hit in data["hits"]]

    # Same document pool, different order.
    assert set(order_plain) == set(order_div)
    assert order_plain != order_div

    # Diversification debug info is exposed per hit.
    for hit in data["hits"]:
        assert hit["debug"]["diversified"] is True
        assert "lambda_param" in hit["debug"]
        assert "diversity_score" in hit["debug"]
        assert "adaptive" in hit["debug"]


@pytest.mark.integration
async def test_mmr_diversify_disabled_flag(wired_app):
    """Test MMR is skipped when the diversify_enabled feature flag is off."""
    app, client = wired_app
    lex_hits, vec_hits = _make_hits()

    with (
        patch("app.search.orchestrator.lexical_search", new=AsyncMock(return_value=lex_hits)),
        patch("app.search.orchestrator.vector_search", new=AsyncMock(return_value=vec_hits)),
        patch(
            "app.api.routes.search._get_embedding_service",
            new=lambda: _mock_embedding(lex_hits, vec_hits),
        ),
        patch.object(settings.feature_flags, "diversify_enabled", False),
    ):
        req = SearchRequest(
            query="test query",
            tenant_id=uuid4(),
            top_k=3,
            diversify=True,
            explain=True,
        )
        response = await client.post("/search", json=req.model_dump(mode="json"))

    assert response.status_code == 200
    for hit in response.json()["hits"]:
        assert hit["debug"]["diversified"] is False
        assert hit["debug"]["lambda_param"] is None


@pytest.mark.integration
async def test_mmr_diversify_lambda_override(wired_app):
    """Test the diversify_lambda request parameter overrides the config default."""
    app, client = wired_app
    lex_hits, vec_hits = _make_hits()

    with (
        patch("app.search.orchestrator.lexical_search", new=AsyncMock(return_value=lex_hits)),
        patch("app.search.orchestrator.vector_search", new=AsyncMock(return_value=vec_hits)),
        patch(
            "app.api.routes.search._get_embedding_service",
            new=lambda: _mock_embedding(lex_hits, vec_hits),
        ),
    ):
        req = SearchRequest(
            query="test query",
            tenant_id=uuid4(),
            top_k=3,
            diversify=True,
            diversify_lambda=0.3,
            explain=True,
        )
        response = await client.post("/search", json=req.model_dump(mode="json"))

    assert response.status_code == 200
    for hit in response.json()["hits"]:
        assert hit["debug"]["lambda_param"] == 0.3


@pytest.mark.integration
async def test_mmr_regression_no_diversify(wired_app):
    """Test that a request without diversify keeps the plain fusion order."""
    app, client = wired_app
    lex_hits, vec_hits = _make_hits()

    with (
        patch("app.search.orchestrator.lexical_search", new=AsyncMock(return_value=lex_hits)),
        patch("app.search.orchestrator.vector_search", new=AsyncMock(return_value=vec_hits)),
        patch(
            "app.api.routes.search._get_embedding_service",
            new=lambda: _mock_embedding(lex_hits, vec_hits),
        ),
    ):
        req = SearchRequest(
            query="test query",
            tenant_id=uuid4(),
            top_k=3,
            diversify=False,
            explain=True,
        )
        response = await client.post("/search", json=req.model_dump(mode="json"))

    assert response.status_code == 200
    for hit in response.json()["hits"]:
        assert hit["debug"]["diversified"] is False
        assert hit["debug"]["diversity_score"] is None
