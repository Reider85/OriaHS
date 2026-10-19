"""Personalization service for user profile vectors and EMA updates.

ARCHITECT §10.1–10.4, PR-01: User profile vector (EMA, cold-start, GDPR).

This module provides the PersonalizationService for:
- User profile vector management (aggregated embeddings)
- EMA updates on click events
- Personalized re-ranking of search results
- GDPR compliance (export/delete)

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-01.
"""

from app.services.personalization import PersonalizationService

__all__ = ["PersonalizationService"]