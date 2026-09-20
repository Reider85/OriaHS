from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import EmbeddingConfig
from app.embedding.service import EmbeddingService
from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
def embedding_service() -> EmbeddingService:
    return EmbeddingService(config=EmbeddingConfig(device="cpu"))
