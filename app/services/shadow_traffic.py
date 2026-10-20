"""Shadow traffic service for parallel strategy evaluation.

ARCHITECT §11.6, PR-08: Shadow traffic (параллельный расчёт новой стратегии).

This module provides the ShadowTrafficService for:
- Parallel execution of alternative search strategies
- Comparison of results vs production strategy
- Logging of shadow variants for analysis

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-08.
"""

from app.services.shadow_traffic import ShadowTrafficService

__all__ = ["ShadowTrafficService"]
