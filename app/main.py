"""FastAPI application factory.

P-12: real health/readiness endpoints plus Prometheus ``/metrics``. The
temporary P-00 root endpoint is gone; ``/health/*`` and ``/metrics`` are
excluded from HTTP instrumentation so probes do not pollute dashboards.
"""

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import health, index, search
from app.config import settings
from app.observability import metrics  # noqa: F401 - registers index_lag_seconds


def create_app() -> FastAPI:
    """Build the FastAPI application (factory, never a global instance)."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )

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
