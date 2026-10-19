"""Distributed tracing with OpenTelemetry.

ARCHITECT §11.2, PR-06: Distributed tracing OpenTelemetry.

This module provides tracing setup and utilities:
- OpenTelemetry SDK configuration
- Span creation and management
- Export to Tempo
- Trace correlation with logs

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-06.
"""

from app.observability.tracing import setup_tracing, get_tracer

__all__ = ["setup_tracing", "get_tracer"]