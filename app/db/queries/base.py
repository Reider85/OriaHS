"""Base class for all query modules (ARCHITECT §4.3)."""

from abc import ABC
from typing import Any


class BaseQueries(ABC):
    """Base class for all query modules providing common functionality."""

    def __init__(self):
        """Initialize the query module."""
        pass

    def _increment_metric(self, metric: Any, **labels: Any) -> None:
        """Increment a Prometheus metric with labels."""
        if hasattr(metric, "labels"):
            metric.labels(**labels).inc()
        else:
            metric.inc()
