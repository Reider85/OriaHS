"""Fusion strategies for hybrid search (P-11, ARCHITECT §7.1, §7.2).

Two fusion functions available on MVP:

- **RRF** (Reciprocal Rank Fusion) — default. Score = sum of 1/(k+rank)
  across lexical and vector lists. Robust, no calibration needed.
- **Weighted** — min-max normalized weighted combination of per-channel scores.
  Ships as a stub for the Critical phase (ARCHITECT §7.2).
"""

from collections import defaultdict
from uuid import UUID

from app.search.lexical import LexicalHit
from app.search.vector import VectorHit


def rrf_fuse(
    lex_hits: list[LexicalHit],
    vec_hits: list[VectorHit],
    k: int = 60,
) -> list[tuple[UUID, float]]:
    """Reciprocal Rank Fusion (ARCHITECT §7.1).

    Merges two ranked lists by position: each document receives a score
    of ``1 / (k + rank)`` from every list it appears in. Documents present
    in *both* channels receive the sum of both contributions.

    Parameters
    ----------
    lex_hits:
        Results from the lexical channel, already ranked best→worst.
    vec_hits:
        Results from the vector channel, already ranked best→worst.
    k:
        Smoothness constant (default 60 per ARCHITECT §7.1).

    Returns
    -------
    list of ``(doc_id, rrf_score)`` sorted descending by score.
    """
    scores: dict[UUID, float] = defaultdict(float)
    for rank, lex_hit in enumerate(lex_hits, start=1):
        scores[lex_hit.doc_id] += 1.0 / (k + rank)
    for rank, vec_hit in enumerate(vec_hits, start=1):
        scores[vec_hit.doc_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


def weighted_fuse(
    lex_hits: list[LexicalHit],
    vec_hits: list[VectorHit],
    alpha: float = 0.5,
) -> list[tuple[UUID, float]]:
    """Weighted fusion with min-max normalization (ARCHITECT §7.2).

    ``alpha`` controls the lexical weight:
    ``alpha * norm(lex) + (1-alpha) * norm(vec)``.

    .. note::
       Stubbed for MVP — weighted fusion is a Critical-phase feature
       (ARCHITECT §7.2, ROADMAP §4). Included here to keep the interface
       complete; the orchestrator only dispatches to ``rrf_fuse`` on MVP.

    Parameters
    ----------
    lex_hits:
        Results from the lexical channel.
    vec_hits:
        Results from the vector channel.
    alpha:
        Weight for the lexical channel, ``0 ≤ alpha ≤ 1``.

    Returns
    -------
    list of ``(doc_id, weighted_score)`` sorted descending by score.
    """

    def _norm(items: list[tuple[UUID, float]]) -> dict[UUID, float]:
        if not items:
            return {}
        scores = [s for _, s in items]
        lo, hi = min(scores), max(scores)
        rng = hi - lo or 1.0
        return {d: (s - lo) / rng for d, s in items}

    lex_pairs = [(h.doc_id, h.score) for h in lex_hits]
    vec_pairs = [(h.doc_id, h.score) for h in vec_hits]
    n_lex = _norm(lex_pairs)
    n_vec = _norm(vec_pairs)

    all_ids = set(n_lex) | set(n_vec)
    fused = [(d, alpha * n_lex.get(d, 0.0) + (1 - alpha) * n_vec.get(d, 0.0)) for d in all_ids]
    return sorted(fused, key=lambda x: -x[1])
