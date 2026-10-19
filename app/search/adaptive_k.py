"""Adaptive K parameter selection per tenant.

ARCHITECT §6.4, PR-10: Adaptive K по tenant_search_stats.

This module provides adaptive K selection:
- Tenant document count thresholds
- Query length adaptation
- Dynamic K_lexical/K_vector adjustment
- Performance optimization

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-10.
"""

from app.search.adaptive_k import get_adaptive_k

__all__ = ["get_adaptive_k"]