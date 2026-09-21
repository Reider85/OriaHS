"""FastAPI application factory.

P-00: minimal skeleton. Only a temporary root endpoint and stub health
router. README documents how to run. P-12 replaces the root endpoint.
"""

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import health, index, search
from app.config import settings


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

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        """Temporary root endpoint; removed in P-12."""
        return {"status": "ok"}

    Instrumentator().instrument(app).expose(
        app,
        endpoint=settings.observability.metrics_path,
    )

    return app


app = create_app()
