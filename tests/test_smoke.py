from httpx import AsyncClient

from app.main import create_app


def test_create_app_factory() -> None:
    """create_app() returns a configured FastAPI instance."""
    app = create_app()
    assert app.title == "OriaHS"


async def test_root_returns_ok(client: AsyncClient) -> None:
    """Temporary root endpoint returns {\"status\": \"ok\"}."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_health_live_ok(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_metrics_exposed(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "http_request_duration_seconds" in resp.text
