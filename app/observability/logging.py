"""Structured JSON logging setup (ARCHITECT §11.4, P-15).

Provides ``setup_logging()`` to configure structlog into a unified
JSON output with contextvars-injected ``request_id``, ``tenant_id``, and
``trace_id`` fields.  ``get_logger(name)`` returns a structlog-bound logger
that automatically merges these context fields into every log entry.

The ``trace_id`` is a placeholder equal to ``request_id``; it will be
replaced by a real OTel trace ID in Production-Ready (ARCHITECT §11.2).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

import structlog

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
tenant_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tenant_id", default=""
)
trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)


def _json_renderer(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> str:
    """Render the event dict as a single-line JSON string."""
    event_dict["timestamp"] = datetime.now(UTC).isoformat()
    event_dict["level"] = method_name.upper()
    event_dict["request_id"] = request_id_ctx.get("")
    event_dict["tenant_id"] = tenant_id_ctx.get("")
    event_dict["trace_id"] = trace_id_ctx.get("")
    return json.dumps(event_dict, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog for structured JSON output.

    Call once at application startup (``create_app()`` or CLI ``__main__``).
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _json_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "") -> Any:
    """Return a structlog logger bound to *name*.

    Every log entry will automatically include ``request_id``, ``tenant_id``,
    and ``trace_id`` from the current contextvars.
    """
    return structlog.get_logger(name)


__all__ = [
    "get_logger",
    "request_id_ctx",
    "setup_logging",
    "tenant_id_ctx",
    "trace_id_ctx",
]
