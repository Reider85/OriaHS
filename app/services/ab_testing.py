"""A/B testing framework for search experiments.

ARCHITECT §11.3, PR-03: A/B-фреймворк (sticky distribution, sequential testing).

This module provides the ABTestingService for:
- Sticky user distribution to experiment variants
- Sequential hypothesis testing
- Experiment management and metrics collection

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-03.
"""

from app.services.ab_testing import ABTestingService

__all__ = ["ABTestingService"]