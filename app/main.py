"""FastAPI application factory.

P-12: real health/readiness endpoints plus Prometheus ``/metrics``. The
temporary P-00 root endpoint is gone; ``/health/*`` and ``/metrics`` are
excluded from HTTP instrumentation so probes do not pollute dashboards.

P-15: structured JSON logging (``setup_logging()``) and ``RequestIDMiddleware``
for automatic ``request_id`` / ``trace_id`` context propagation.
"""

import logging
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.middleware.request_id import RequestIDMiddleware
from app.api.routes import health, index, search
from app.config import settings
from app.observability import metrics  # noqa: F401 - registers all Prometheus collectors
from app.observability.logging import setup_logging
from app.reranker.service import RerankerService


async def lifespan(app: FastAPI):
    """Application lifespan events (C-01: optional reranker warmup)."""
    if settings.reranker.warmup:
        logger = logging.getLogger(__name__)
        logger.info("Warming up reranker model...")
        reranker = RerankerService(config=settings.reranker)
        await reranker.warmup()
        logger.info("Reranker warmup complete.")
    
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application (factory, never a global instance)."""
    setup_logging(level=settings.observability.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)

    app.include_router(health.router)
    app.include_router(index.router)
    app.include_router(search.router)

    Instrumentator(
        excluded_handlers=["/health", settings.observability.metrics_path]
    ).instrument(app).expose(
        app,
        endpoint=settings.observability.metrics_path,
    )

    return app


app = create_app()
