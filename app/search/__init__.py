"""Search subsystem.

P-04 lays the Qdrant client + payload schema, P-09 the lexical channel,
P-10 the vector channel, P-11 RRF fusion and orchestrator, C-04 speculative rerank.
"""

from app.search.filters import SearchFilters
from app.search.fusion import rrf_fuse, weighted_fuse
from app.search.lexical import LexicalHit, lexical_search
from app.search.speculative import SpeculativeReranker
from app.search.vector import VectorHit, build_qdrant_filter, vector_search

__all__ = [
    "SearchFilters",
    "LexicalHit",
    "lexical_search",
    "SpeculativeReranker",
    "VectorHit",
    "build_qdrant_filter",
    "vector_search",
    "rrf_fuse",
    "weighted_fuse",
]
