"""Distributed tracing with OpenTelemetry.

ARCHITECT §11.2, PR-06: Distributed tracing OpenTelemetry.

This module provides tracing setup and utilities:
- OpenTelemetry SDK configuration
- Span creation and management
- Export to Tempo
- Trace correlation with logs
"""

import hashlib
import logging
from contextlib import contextmanager
from typing import Any, ContextManager

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Tracer

from app.config import FeatureFlags, TracingConfig
from app.observability.metrics import CountingSpanExporter

logger = logging.getLogger(__name__)


def setup_tracing(app: Any, config: TracingConfig, feature_flags: FeatureFlags) -> None:
    """Initialize OpenTelemetry tracing for FastAPI.

    Parameters
    ----------
    app
        FastAPI application instance.
    config
        Tracing configuration from settings.
    feature_flags
        Global feature flags.
    """
    if not feature_flags.tracing_enabled:
        logger.info("Tracing disabled via feature flag")
        return

    # Configure resource
    resource = Resource(attributes={
        SERVICE_NAME: config.service_name,
    })

    # Configure tracer provider with sampling
    provider = TracerProvider(
        resource=resource,
        sampler=trace.sampling.ParentBased(
            trace.sampling.TraceIdRatioBased(config.sample_rate)
        ),
    )

    # Configure span processor with batch export
    base_exporter = OTLPSpanExporter(
        endpoint=config.otel_endpoint,
        insecure=True,
    )

    # Wrap exporter with Prometheus metrics counter
    counting_exporter = CountingSpanExporter(base_exporter, job_name=config.service_name)

    span_processor = BatchSpanProcessor(
        counting_exporter,
        export_timeout_ms=config.export_interval_ms,
    )
    provider.add_span_processor(span_processor)

    # Register the provider
    trace.set_tracer_provider(provider)

    # Register instrumentations
    FastAPIInstrumentor().instrument_app(
        app,
        excluded_handlers=["/health", "/metrics"],
        tracer_provider=provider,
    )
    RedisInstrumentor().instrument(tracer_provider=provider)

    # Register logging handler for OTel logs
    _setup_logging_handler()

    logger.info(
        "OpenTelemetry tracing initialized",
        extra={
            "otel_endpoint": config.otel_endpoint,
            "sample_rate": config.sample_rate,
            "export_interval_ms": config.export_interval_ms,
        },
    )


def get_tracer(name: str) -> Tracer:
    """Get a tracer instance for creating spans.

    Parameters
    ----------
    name
        Instrumentation name (e.g., "search.request", "embedding.query").

    Returns
    -------
    Tracer
        OpenTelemetry tracer instance.
    """
    return trace.get_tracer(name)


@contextmanager
def span(tracer: Tracer, name: str, attributes: dict[str, Any] | None = None) -> ContextManager[Span]:
    """Create a span with automatic start/end.

    Parameters
    ----------
    tracer
        Tracer instance from get_tracer().
    name
        Span name (e.g., "search.request", "pg.lexical").
    attributes
        Optional span attributes (key-value pairs).

    Yields
    ------
    Span
        The created span (active context).
    """
    with tracer.start_span(name, attributes=attributes or {}) as span:
        yield span


def hash_user_id(user_id: Any) -> str:
    """Hash user_id for trace attributes (privacy: never store raw user_id).

    Parameters
    ----------
    user_id
        User identifier (UUID, str, etc.).

    Returns
    -------
    str
        SHA256 hex hash of user_id string representation.
    """
    user_id_str = str(user_id) if user_id is not None else ""
    return hashlib.sha256(user_id_str.encode()).hexdigest()


def _setup_logging_handler() -> None:
    """Bridge structlog to OTel logs for trace correlation."""
    import logging as stdlib_logging

    from opentelemetry.sdk.logging import LoggingHandler

    # Create OTel logger
    otel_logger = stdlib_logging.getLogger("oriahs.structlog")
    otel_logger.setLevel(stdlib_logging.INFO)

    # Add OTel logging handler to root logger
    # This captures stdlib logging calls and emits them as OTel logs
    handler = LoggingHandler(level=stdlib_logging.INFO)
    stdlib_logging.getLogger().addHandler(handler)

    # Also capture structlog events via a processor
    # This ensures structlog events are also emitted as OTel logs
    def otel_log_processor(
        logger: Any,
        method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Emit structlog events as OTel logs."""
        # Skip if already handled by OTel handler (avoid duplicates)
        if not event_dict.get("_otel_handled", False):
            # Get current span context for correlation
            current_span = trace.get_current_span()
            span_context = current_span.get_span_context() if current_span else None

            # Create OTel log record
            otel_logger.log(
                getattr(stdlib_logging, method_name.upper(), stdlib_logging.INFO),
                event_dict["event"],
                extra={
                    "otel.event.name": event_dict.get("event", ""),
                    "otel.event.attributes": {
                        k: v for k, v in event_dict.items()
                        if k not in ("event", "timestamp", "level", "logger")
                    },
                    **({"trace.id": span_context.trace_id} if span_context else {}),
                    **({"span.id": span_context.span_id} if span_context else {}),
                }
            )
            event_dict["_otel_handled"] = True

        return event_dict

    # Add processor to structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            otel_log_processor,  # Add OTel processor
            structlog.processors.JSONRenderer(),  # Keep JSON output
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(stdlib_logging, logging.getLogger().getEffectiveLevel(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=stdlib_logging.StreamHandler().stream),
        cache_logger_on_first_use=True,
    )


__all__ = [
    "setup_tracing",
    "get_tracer",
    "span",
    "hash_user_id",
]
