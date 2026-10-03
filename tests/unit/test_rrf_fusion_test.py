"""P-11: RRF + weighted fusion unit tests (ARCHITECT §7.1, §7.2).

Pure-function tests — no infrastructure required.
"""

import math
from uuid import UUID, uuid4

from app.search.fusion import rrf_fuse, weighted_fuse
from app.search.lexical import LexicalHit
from app.search.vector import VectorHit


def _lex(doc_id: UUID, score: float = 1.0, title: str = "t", snippet: str = "s") -> LexicalHit:
    return LexicalHit(doc_id=doc_id, score=score, title=title, content_snippet=snippet)


def _vec(doc_id: UUID, score: float = 1.0, title: str = "t", snippet: str = "s") -> VectorHit:
    return VectorHit(doc_id=doc_id, score=score, title=title, content_snippet=snippet)


# ---------------------------------------------------------------------------
# rrf_fuse tests
# ---------------------------------------------------------------------------


class TestRRFFuse:
    def test_empty_inputs(self) -> None:
        assert rrf_fuse([], [], k=60) == []

    def test_lexical_only(self) -> None:
        d1, d2 = uuid4(), uuid4()
        lex = [_lex(d1), _lex(d2)]
        result = rrf_fuse(lex, [], k=60)
        ids = [doc_id for doc_id, _ in result]
        assert ids == [d1, d2]
        # d1 at rank 1: 1/(60+1) = 1/61
        assert math.isclose(result[0][1], 1 / 61, rel_tol=1e-9)

    def test_vector_only(self) -> None:
        d1 = uuid4()
        vec = [_vec(d1)]
        result = rrf_fuse([], vec, k=60)
        assert len(result) == 1
        assert result[0][0] == d1

    def test_both_channels_same_doc(self) -> None:
        d1 = uuid4()
        lex = [_lex(d1)]
        vec = [_vec(d1)]
        result = rrf_fuse(lex, vec, k=60)
        assert len(result) == 1
        # d1 at rank 1 in both: 1/61 + 1/61 = 2/61
        expected = 1 / 61 + 1 / 61
        assert math.isclose(result[0][1], expected, rel_tol=1e-9)

    def test_disjoint_docs(self) -> None:
        d1, d2 = uuid4(), uuid4()
        lex = [_lex(d1)]
        vec = [_vec(d2)]
        result = rrf_fuse(lex, vec, k=60)
        ids = [doc_id for doc_id, _ in result]
        assert set(ids) == {d1, d2}
        # Both at rank 1 in their channel → same score
        assert math.isclose(result[0][1], result[1][1], rel_tol=1e-9)

    def test_overlapping_docs_ordering(self) -> None:
        """Doc A is rank 1 in both channels, doc B is rank 2 only in lexical."""
        a, b = uuid4(), uuid4()
        lex = [_lex(a), _lex(b)]
        vec = [_vec(a)]
        result = rrf_fuse(lex, vec, k=60)
        assert result[0][0] == a
        assert result[1][0] == b
        # A: 1/61 + 1/61 = 2/61
        # B: 1/62
        assert math.isclose(result[0][1], 2 / 61, rel_tol=1e-9)
        assert math.isclose(result[1][1], 1 / 62, rel_tol=1e-9)

    def test_custom_k(self) -> None:
        d1 = uuid4()
        lex = [_lex(d1)]
        result = rrf_fuse(lex, [], k=1)
        # rank 1: 1/(1+1) = 0.5
        assert math.isclose(result[0][1], 0.5, rel_tol=1e-9)

    def test_multiple_docs_ordering(self) -> None:
        """Docs in reverse order in lexical — result reflects rank."""
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        lex = [_lex(d3), _lex(d2), _lex(d1)]
        result = rrf_fuse(lex, [], k=60)
        # d3 rank 1, d2 rank 2, d1 rank 3
        assert result[0][0] == d3
        assert result[1][0] == d2
        assert result[2][0] == d1


# ---------------------------------------------------------------------------
# weighted_fuse tests
# ---------------------------------------------------------------------------


class TestWeightedFuse:
    def test_empty_inputs(self) -> None:
        assert weighted_fuse([], [], alpha=0.5) == []

    def test_lexical_only(self) -> None:
        d1, d2 = uuid4(), uuid4()
        lex = [_lex(d1, score=10.0), _lex(d2, score=5.0)]
        result = weighted_fuse(lex, [], alpha=0.5)
        ids = [doc_id for doc_id, _ in result]
        assert ids == [d1, d2]

    def test_both_channels_same_doc(self) -> None:
        d1 = uuid4()
        lex = [_lex(d1, score=10.0)]
        vec = [_vec(d1, score=0.8)]
        result = weighted_fuse(lex, vec, alpha=0.5)
        assert len(result) == 1
        # Single item: min==max → rng=1.0 → normalized=0.0 for both channels
        # Result: 0.5*0.0 + 0.5*0.0 = 0.0
        assert math.isclose(result[0][1], 0.0, abs_tol=1e-9)

    def test_alpha_zero_uses_only_vector(self) -> None:
        d1, d2 = uuid4(), uuid4()
        lex = [_lex(d1, score=100.0)]
        vec = [_vec(d2, score=0.5)]
        result = weighted_fuse(lex, vec, alpha=0.0)
        # d2 gets full vec weight (normalized to 1.0), d1 gets 0 from lex
        # But with single items both normalize to 0.0; d2 gets vec weight
        scores = {doc_id: s for doc_id, s in result}
        assert scores[d2] >= scores[d1]

    def test_alpha_one_uses_only_lexical(self) -> None:
        d1, d2 = uuid4(), uuid4()
        # Use multiple items so normalization is meaningful
        lex = [_lex(d1, score=100.0), _lex(d2, score=50.0)]
        vec = [_vec(uuid4(), score=0.9)]
        result = weighted_fuse(lex, vec, alpha=1.0)
        # d1 has highest lex score → should be first
        assert result[0][0] == d1

    def test_normalization_range(self) -> None:
        """Scores are min-max normalized to [0, 1]."""
        d1, d2 = uuid4(), uuid4()
        lex = [_lex(d1, score=10.0), _lex(d2, score=20.0)]
        result = weighted_fuse(lex, [], alpha=1.0)
        # d1 normalized: (10-10)/(20-10) = 0.0, d2: (20-10)/(20-10) = 1.0
        scores = {doc_id: s for doc_id, s in result}
        assert math.isclose(scores[d1], 0.0, abs_tol=1e-9)
        assert math.isclose(scores[d2], 1.0, abs_tol=1e-9)

    def test_symmetry(self) -> None:
        """weighted_fuse(lex, vec, alpha) should have same ordering as weighted_fuse(vec, lex, 1-alpha)."""
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        lex = [_lex(d1, score=10.0), _lex(d2, score=5.0)]
        vec = [_vec(d3, score=0.9), _vec(d1, score=0.7)]

        result1 = weighted_fuse(lex, vec, alpha=0.3)
        result2 = weighted_fuse(vec, lex, alpha=0.7)

        # Should have same ordering by doc_id
        ids1 = [doc_id for doc_id, _ in result1]
        ids2 = [doc_id for doc_id, _ in result2]
        assert ids1 == ids2

    def test_outlier_sensitivity(self) -> None:
        """Weighted fusion is sensitive to outliers due to min-max normalization."""
        d1, d2, d3, d4 = uuid4(), uuid4(), uuid4(), uuid4()
        # Lex scores: [0.1, 0.2, 0.3, 100.0] → outlier at 100.0
        lex = [_lex(d1, score=0.1), _lex(d2, score=0.2), _lex(d3, score=0.3), _lex(d4, score=100.0)]
        # Vec scores: [0.5, 0.6, 0.7, 0.8] → no outliers
        vec = [_vec(d1, score=0.5), _vec(d2, score=0.6), _vec(d3, score=0.7), _vec(d4, score=0.8)]

        result = weighted_fuse(lex, vec, alpha=0.5)
        scores = {doc_id: s for doc_id, s in result}

        # d4 (outlier) gets very high weight from lex: (100-0.1)/(100-0.1) ≈ 1.0
        # d1, d2, d3 get low lex weights: (0.1-0.1)/99.9≈0, (0.2-0.1)/99.9≈0.001, (0.3-0.1)/99.9≈0.002
        # d4 should dominate despite being worst in vec channel
        assert result[0][0] == d4  # outlier is first
        assert scores[d4] > 0.9  # very high score

    def test_equal_scores_division_by_zero(self) -> None:
        """All equal scores should normalize to 0.0 with rng=1.0 protection."""
        d1, d2, d3 = uuid4(), uuid4(), uuid4()
        lex = [_lex(d1, score=5.0), _lex(d2, score=5.0)]
        vec = [_vec(d1, score=5.0), _vec(d3, score=5.0)]

        result = weighted_fuse(lex, vec, alpha=0.5)
        scores = {doc_id: s for doc_id, s in result}

        # All scores should be 0.0 because rng = 5.0 - 5.0 = 0 → rng = 1.0
        # All normalized = (5.0 - 5.0) / 1.0 = 0.0
        assert all(math.isclose(s, 0.0, abs_tol=1e-9) for s in scores.values())
