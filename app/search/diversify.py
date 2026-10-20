"""MMR diversification for anti-bubble search results.

ARCHITECT §10.5, PR-04: MMR anti-bubble diversification.

This module provides diversification utilities:
- Maximal Marginal Relevance (MMR) algorithm
- Anti-bubble prevention
- Adaptive lambda parameter
- Diversified result ranking

Exposed functions:
- `mmr_diversify(candidates, doc_vectors, lambda_param=0.7, top_k=20) -> list[tuple[UUID, float]]`
  Spec-compliant interface for PR-04.
- `mmr_diversify_detailed(candidates, doc_vectors, lambda_param=0.7, top_k=20, lambda_adaptive=True) -> MMRResult`
  Rich interface for orchestrator (returns diversity_score, lambda_used, adaptive).
- `compute_diversity_score(vectors) -> float`
  Computes mean(1 - max_sim) for diversity evaluation.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)

#: Relevance weight below which the result set is considered a "bubble".
DIVERSITY_THRESHOLD = 0.3

#: Step applied to lambda when the diversity score falls below the threshold.
LAMBDA_ADAPT_STEP = 0.1

#: Lower bound for lambda — below this the ranking degrades into pure noise.
LAMBDA_MIN = 0.5


@dataclass
class MMRResult:
    """Rich result from MMR diversification with debug/metadata."""

    doc_ids: list[tuple[UUID, float]]
    lambda_used: float
    diversity_score: float | None
    adaptive: bool


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize every row of a matrix (zero rows are left untouched)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms == 0.0, 1.0, norms)
    return matrix / safe


def compute_diversity_score(doc_vectors: np.ndarray, top_k: int = 10) -> float:
    """Compute diversity_score = mean(1 - max_sim) for top-K documents.

    Args:
        doc_vectors: Matrix of shape (N, D). Rows are L2-normalized internally,
            so un-normalized embeddings are handled correctly.
        top_k: Use top-K vectors for diversity computation (min(top_k, N)).

    Returns:
        Mean diversity score in [0, 1]; 1.0 for a single document, 0.0 for none.
    """
    n = min(len(doc_vectors), top_k)
    if n < 2:
        return 1.0 if n == 1 else 0.0

    # Cosine similarity == dot product for L2-normalized vectors.
    normalized = _unit_rows(np.asarray(doc_vectors[:n], dtype=np.float32))
    similarities = normalized @ normalized.T
    np.fill_diagonal(similarities, -1.0)  # exclude self-similarity
    max_similarities = np.max(similarities, axis=1)

    return float(np.mean(1.0 - max_similarities))


def _normalize_scores(scores: np.ndarray) -> np.ndarray:
    """Min-max normalize relevance scores into [0, 1]."""
    if len(scores) > 1 and np.max(scores) > np.min(scores):
        spread = float(np.max(scores) - np.min(scores))
        return np.asarray((scores - np.min(scores)) / spread, dtype=np.float32)
    # All scores equal → neutral relevance, so diversity fully decides.
    return np.full_like(scores, 0.5)


def _greedy_select(
    candidates: list[tuple[UUID, float, float]],
    doc_vectors: dict[UUID, np.ndarray],
    lambda_param: float,
    top_k: int,
) -> list[UUID]:
    """Greedy MMR selection, vectorized over the candidate pool.

    Complexity is O(K * N * D) with numpy dot products (K selections, N
    candidates, D dimensions) — well inside the 5 ms budget for K=20, D=1024.

    Args:
        candidates: List of (doc_id, raw_score, normalized_relevance) tuples.
        doc_vectors: Mapping doc_id → embedding vector.
        lambda_param: Relevance weight (0.0 = pure diversity, 1.0 = pure relevance).
        top_k: Maximum number of documents to select.

    Returns:
        List of selected doc_ids in MMR order.
    """
    if not candidates or top_k <= 0:
        return []

    ids = [candidate[0] for candidate in candidates]
    matrix = _unit_rows(
        np.asarray([doc_vectors[doc_id] for doc_id in ids], dtype=np.float32)
    )
    relevance = np.asarray([candidate[2] for candidate in candidates], dtype=np.float32)

    remaining = np.ones(len(ids), dtype=bool)
    selected_ids: list[UUID] = []
    selected_matrix = np.empty((0, matrix.shape[1]), dtype=np.float32)

    for _ in range(min(top_k, len(ids))):
        if selected_matrix.shape[0] == 0:
            max_sim = np.zeros(len(ids), dtype=np.float32)
        else:
            # (N, |S|) similarity of every candidate to every selected doc.
            max_sim = (matrix @ selected_matrix.T).max(axis=1)

        mmr_scores = lambda_param * relevance - (1.0 - lambda_param) * max_sim
        mmr_scores[~remaining] = -np.inf

        best_index = int(np.argmax(mmr_scores))
        remaining[best_index] = False
        selected_ids.append(ids[best_index])
        selected_matrix = np.vstack([selected_matrix, matrix[best_index]])

    return selected_ids


def mmr_diversify_detailed(
    candidates: list[tuple[UUID, float]],
    doc_vectors: dict[UUID, np.ndarray],
    lambda_param: float = 0.7,
    top_k: int = 20,
    lambda_adaptive: bool = True,
    diversity_threshold: float = DIVERSITY_THRESHOLD,
    lambda_step: float = LAMBDA_ADAPT_STEP,
    min_lambda: float = LAMBDA_MIN,
) -> MMRResult:
    """MMR diversification with adaptive lambda and debug info.

    Args:
        candidates: List of (doc_id, relevance_score) tuples, unsorted.
        doc_vectors: Mapping doc_id → L2-normalized vector (shape (1024,)).
        lambda_param: Relevance weight (0.0 = pure diversity, 1.0 = pure relevance).
        top_k: Number of results to return.
        lambda_adaptive: If True, adapt lambda based on diversity_score.
        diversity_threshold: Diversity score below which λ is lowered.
        lambda_step: Decrement applied to λ per adaptive iteration.
        min_lambda: Lower bound for λ.

    Returns:
        MMRResult with reordered doc_ids, lambda_used, diversity_score, adaptive.
    """
    if len(candidates) <= 1:
        return MMRResult(
            doc_ids=list(candidates),
            lambda_used=lambda_param,
            diversity_score=None,
            adaptive=False,
        )

    # Filter candidates with missing vectors (skip, warn).
    valid_candidates: list[tuple[UUID, float]] = []
    for doc_id, score in candidates:
        if doc_id not in doc_vectors:
            logger.warning(
                "MMR skipping document %s: missing vector",
                doc_id,
                extra={"doc_id": str(doc_id)},
            )
            continue
        valid_candidates.append((doc_id, score))

    if not valid_candidates:
        logger.warning("MMR: no valid candidates after filtering")
        return MMRResult(
            doc_ids=[],
            lambda_used=lambda_param,
            diversity_score=None,
            adaptive=False,
        )

    # Sort by relevance desc.
    candidates_sorted = sorted(valid_candidates, key=lambda item: -item[1])

    # Min-max normalize relevance scores to [0, 1] for λ semantics
    # (RRF scores are ~0.016, cosines are 0..1 — without this λ is meaningless).
    scores = np.array([score for _, score in candidates_sorted], dtype=np.float32)
    scores_norm = _normalize_scores(scores)

    pool: list[tuple[UUID, float, float]] = [
        (doc_id, score, float(relevance))
        for (doc_id, score), relevance in zip(candidates_sorted, scores_norm, strict=True)
    ]

    def select(lam: float) -> list[UUID]:
        return _greedy_select(pool, doc_vectors, lam, top_k)

    final_lambda = lambda_param
    selected_ids = select(final_lambda)

    def diversity_of(ids: list[UUID]) -> float:
        vectors = np.array([doc_vectors[doc_id] for doc_id in ids], dtype=np.float32)
        return compute_diversity_score(vectors)

    diversity_score = diversity_of(selected_ids) if len(selected_ids) >= 2 else None
    adaptive = False

    if lambda_adaptive and diversity_score is not None:
        # Bubble detected → lower λ (more diversity) and recompute, down to the floor.
        while diversity_score < diversity_threshold and final_lambda > min_lambda:
            final_lambda = max(min_lambda, final_lambda - lambda_step)
            adaptive = True
            selected_ids = select(final_lambda)
            if len(selected_ids) >= 2:
                diversity_score = diversity_of(selected_ids)
            else:
                break

    score_by_id = dict(candidates_sorted)
    result = [(doc_id, score_by_id[doc_id]) for doc_id in selected_ids]

    return MMRResult(
        doc_ids=result,
        lambda_used=final_lambda,
        diversity_score=diversity_score,
        adaptive=adaptive,
    )


def mmr_diversify(
    candidates: list[tuple[UUID, float]],
    doc_vectors: dict[UUID, np.ndarray],
    lambda_param: float = 0.7,
    top_k: int = 20,
    lambda_adaptive: bool = True,
) -> list[tuple[UUID, float]]:
    """MMR diversification — spec-compliant interface (PR-04).

    Args:
        candidates: List of (doc_id, relevance_score) tuples.
        doc_vectors: Mapping doc_id → vector.
        lambda_param: Relevance weight.
        top_k: Number of results.
        lambda_adaptive: Enable adaptive lambda.

    Returns:
        List of (doc_id, score) tuples sorted by MMR score.
    """
    result = mmr_diversify_detailed(
        candidates=candidates,
        doc_vectors=doc_vectors,
        lambda_param=lambda_param,
        top_k=top_k,
        lambda_adaptive=lambda_adaptive,
    )
    return result.doc_ids


__all__ = [
    "mmr_diversify",
    "mmr_diversify_detailed",
    "compute_diversity_score",
    "MMRResult",
]
