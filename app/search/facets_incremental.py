"""Incremental facet computation with Redis caching.

ARCHITECT §8.5, PR-11: Инкрементальные фасеты.

This module provides incremental facet utilities:
- Redis caching for facet computations
- Query hash-based cache keys
- Incremental merge of results
- TTL-based cache invalidation

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-11.
"""

from app.search.facets_incremental import compute_incremental_facets

__all__ = ["compute_incremental_facets"]
