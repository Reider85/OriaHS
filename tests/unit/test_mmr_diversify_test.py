"""Unit tests for MMR diversification (PR-04).

Tests MMR algorithm, adaptive lambda, diversity score computation, and edge cases.
Pure numpy operations, no external dependencies.
"""

from uuid import uuid4

import numpy as np

from app.search.diversify import (
    MMRResult,
    compute_diversity_score,
    mmr_diversify,
    mmr_diversify_detailed,
)


def _build_pool() -> tuple[list[tuple[object, float]], dict[object, np.ndarray]]:
    """Build a candidate pool with matching vectors.

    Vector structure: two near-duplicate pairs plus one outlier, so relevance
    ordering and MMR ordering genuinely differ.
    """
    ids = [uuid4() for _ in range(5)]
    vectors = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],  # near-duplicate of the first
            [0.0, 1.0, 0.0],
            [0.0, 0.9, 0.1],  # near-duplicate of the third
            [0.0, 0.0, 1.0],  # outlier
        ],
        dtype=np.float32,
    )
    candidates = [(ids[i], 0.9 - i * 0.1) for i in range(5)]
    doc_vectors = {ids[i]: vectors[i] for i in range(5)}
    return candidates, doc_vectors


class TestComputeDiversityScore:
    """Test diversity score computation."""

    def test_single_doc(self):
        """Test diversity score for single document."""
        vector = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        score = compute_diversity_score(vector[np.newaxis, :])
        assert score == 1.0  # Single doc = max diversity

    def test_empty_vectors(self):
        """Test diversity score for empty vectors."""
        vectors = np.empty((0, 3), dtype=np.float32)
        score = compute_diversity_score(vectors)
        assert score == 0.0

    def test_two_docs_similar(self):
        """Test diversity score for two similar documents."""
        vectors = np.array(
            [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]],
            dtype=np.float32,
        )
        score = compute_diversity_score(vectors, top_k=2)
        assert score < 0.5  # Similar vectors → low diversity

    def test_two_docs_orthogonal(self):
        """Test diversity score for two orthogonal documents."""
        vectors = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            dtype=np.float32,
        )
        score = compute_diversity_score(vectors, top_k=2)
        assert score > 0.9  # Orthogonal vectors → max diversity

    def test_top_k_subset(self):
        """Test diversity score uses the top-K subset."""
        vectors = np.array(
            [
                [1.0, 0.0, 0.0],  # similar to the second
                [0.9, 0.1, 0.0],
                [0.0, 0.0, 1.0],  # different
            ],
            dtype=np.float32,
        )
        score_full = compute_diversity_score(vectors, top_k=3)
        score_top2 = compute_diversity_score(vectors, top_k=2)
        assert score_full > score_top2


class TestMMRDiversify:
    """Test MMR diversification algorithm."""

    def test_passthrough_single_candidate(self):
        """Test MMR returns a single candidate as-is."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify(candidates[:1], doc_vectors)
        assert len(result) == 1
        assert result[0] == candidates[0]

    def test_passthrough_no_candidates(self):
        """Test MMR handles an empty candidate list."""
        result = mmr_diversify([], {})
        assert result == []

    def test_missing_vectors_skipped(self):
        """Test MMR skips candidates with missing vectors."""
        candidates, doc_vectors = _build_pool()
        partial = {candidates[0][0]: doc_vectors[candidates[0][0]]}
        result = mmr_diversify(candidates, partial)
        assert len(result) == 1
        assert result[0][0] == candidates[0][0]

    def test_top_k_limit(self):
        """Test MMR respects the top_k limit."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify(candidates, doc_vectors, top_k=2)
        assert len(result) == 2

    def test_lambda_balancing(self):
        """Test the lambda parameter balances relevance against diversity."""
        candidates, doc_vectors = _build_pool()
        high_lambda = mmr_diversify(
            candidates, doc_vectors, lambda_param=0.95, lambda_adaptive=False
        )
        low_lambda = mmr_diversify(
            candidates, doc_vectors, lambda_param=0.1, lambda_adaptive=False
        )
        assert high_lambda != low_lambda

    def test_pure_relevance_keeps_original_order(self):
        """Test lambda=1.0 preserves the relevance ordering."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify(
            candidates, doc_vectors, lambda_param=1.0, lambda_adaptive=False
        )
        assert [doc_id for doc_id, _ in result] == [doc_id for doc_id, _ in candidates]

    def test_adaptive_lambda_lowers_lambda(self):
        """Test adaptive lambda lowers λ when the result set is a bubble."""
        candidates, doc_vectors = _build_pool()
        # Only near-duplicate vectors → diversity below the threshold.
        bubble_ids = [candidates[0][0], candidates[1][0]]
        bubble_candidates = [candidates[0], candidates[1]]
        bubble_vectors = {doc_id: doc_vectors[doc_id] for doc_id in bubble_ids}

        adaptive = mmr_diversify_detailed(
            bubble_candidates,
            bubble_vectors,
            lambda_param=0.7,
            lambda_adaptive=True,
        )
        static = mmr_diversify_detailed(
            bubble_candidates,
            bubble_vectors,
            lambda_param=0.7,
            lambda_adaptive=False,
        )

        assert adaptive.adaptive is True
        assert adaptive.lambda_used < static.lambda_used
        assert adaptive.lambda_used >= 0.5  # floor respected

    def test_adaptive_disabled_keeps_lambda(self):
        """Test adaptive=False keeps the requested lambda."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify_detailed(
            candidates,
            doc_vectors,
            lambda_param=0.7,
            lambda_adaptive=False,
        )
        assert result.adaptive is False
        assert result.lambda_used == 0.7

    def test_diversity_score_reported(self):
        """Test the diversity score is reported for multi-document results."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify_detailed(candidates, doc_vectors, lambda_adaptive=False)
        assert result.diversity_score is not None
        assert 0.0 <= result.diversity_score <= 1.0

    def test_scores_preserved(self):
        """Test the original relevance scores are preserved in the output."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify(candidates, doc_vectors)
        scores = {doc_id: score for doc_id, score in candidates}
        for doc_id, score in result:
            assert score == scores[doc_id]

    def test_all_candidates_returned(self):
        """Test every candidate is returned when top_k exceeds the pool size."""
        candidates, doc_vectors = _build_pool()
        result = mmr_diversify(candidates, doc_vectors, top_k=50)
        assert len(result) == len(candidates)
        assert {doc_id for doc_id, _ in result} == {doc_id for doc_id, _ in candidates}

    def test_normalized_relevance(self):
        """Test relevance normalization handles wildly different score ranges."""
        candidates, doc_vectors = _build_pool()
        rescaled = [(doc_id, score * 1000.0) for doc_id, score in candidates]
        result = mmr_diversify(rescaled, doc_vectors)
        assert len(result) == len(candidates)

    def test_equal_scores(self):
        """Test equal relevance scores do not break normalization."""
        candidates, doc_vectors = _build_pool()
        flat = [(doc_id, 0.5) for doc_id, _ in candidates]
        result = mmr_diversify(flat, doc_vectors)
        assert len(result) == len(candidates)

    def test_category_diversity(self):
        """Test the top-3 keeps at least 2 distinct categories (ARCHITECT §10.5).

        ``category`` is not a hard filter in the implementation — the soft
        guarantee is expressed through vector diversity, so the test uses
        per-category vectors as a proxy.
        """
        cat1_id, cat2_id, cat3_id = uuid4(), uuid4(), uuid4()

        doc_vectors = {
            cat1_id: np.array([1.0, 0.0, 0.0], dtype=np.float32),
            cat2_id: np.array([0.0, 1.0, 0.0], dtype=np.float32),
            cat3_id: np.array([0.0, 0.0, 1.0], dtype=np.float32),
        }
        # Two documents from category 1, two from category 2, one from category 3.
        candidates = [
            (cat1_id, 0.9),
            (cat2_id, 0.8),
            (cat1_id, 0.75),
            (cat2_id, 0.7),
            (cat3_id, 0.6),
        ]

        result = mmr_diversify(
            candidates, doc_vectors, lambda_param=0.7, top_k=3, lambda_adaptive=False
        )

        # At least 2 distinct categories in the top-3.
        categories = {doc_id for doc_id, _ in result}
        assert len(categories) >= 2

    def test_performance_smoke(self):
        """Test MMR stays within the 5 ms budget for top-20 (ARCHITECT §10.5)."""
        import time

        candidates, doc_vectors = _build_pool()
        # Realistic bge-m3 dimensionality.
        dim = 1024
        big_vectors = {
            doc_id: np.random.default_rng(abs(hash(doc_id)) % (2**32)).random(dim, dtype=np.float32)
            for doc_id in doc_vectors
        }
        large_candidates = candidates * 4
        large_candidates = [(doc_id, score) for doc_id, score in large_candidates]
        for doc_id in doc_vectors:
            large_candidates.append((doc_id, 0.45))

        start = time.time()
        result = mmr_diversify(large_candidates, big_vectors, top_k=20)
        elapsed = time.time() - start

        assert elapsed < 0.005  # 5 ms budget
        assert len(result) == 20


class TestMMRResult:
    """Test the MMRResult dataclass."""

    def test_mmresult_creation(self):
        """Test MMRResult can be created."""
        doc_ids = [(uuid4(), 0.9), (uuid4(), 0.8)]
        result = MMRResult(
            doc_ids=doc_ids,
            lambda_used=0.7,
            diversity_score=0.5,
            adaptive=True,
        )

        assert result.doc_ids == doc_ids
        assert result.lambda_used == 0.7
        assert result.diversity_score == 0.5
        assert result.adaptive is True

    def test_mmresult_optional_fields(self):
        """Test MMRResult handles optional fields correctly."""
        result = MMRResult(
            doc_ids=[(uuid4(), 0.9)],
            lambda_used=0.7,
            diversity_score=None,
            adaptive=False,
        )

        assert result.diversity_score is None
        assert result.adaptive is False
