"""Contract test fixtures (ARCHITECT §15.4, ROADMAP §5.2.14; PR-14).

Provides fixtures for schema drift contract tests: PostgreSQL + Qdrant
containers, seeding, and test-specific stubs.
"""


import pytest

from app.db.models import Base
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.services.qdrant import QdrantService
from tests.integration.conftest import _to_asyncpg_url


@pytest.fixture(scope="session")
def contract_pg() -> str:
    """Session-scoped PostgreSQL container DSN for contract tests."""
    from testcontainers.community.postgres import PostgresContainer
    from urllib.parse import urlsplit
    
    def _to_asyncpg_url(url: str) -> str:
        parts = urlsplit(url)
        host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
        return f"postgresql+asyncpg://{parts.username}:{parts.password}@{host}{parts.path}"
    
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield _to_asyncpg_url(pg.get_connection_url())


@pytest.fixture(scope="function")
async def engine(contract_pg: str):
    """Function-scoped asyncpg engine with contract test setup/teardown."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_to_asyncpg_url(contract_pg))

    # Create tables and seed default model
    async with engine.begin() as conn:
        # Create required extensions
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # Create only required tables (avoid model_migrations with uuid4())
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS documents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                external_ref TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                language TEXT,
                tags TEXT[] DEFAULT '{}',
                attributes JSONB DEFAULT '{}',
                embedding_model TEXT NOT NULL,
                embedding_rev INTEGER DEFAULT 1,
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                deleted_at TIMESTAMP WITH TIME ZONE,
                tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))) STORED
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS embedding_models (
                name TEXT PRIMARY KEY,
                dimension INTEGER NOT NULL,
                description TEXT,
                is_default BOOLEAN DEFAULT false,
                is_active BOOLEAN DEFAULT true,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            )
        """))

        # Insert default model
        await conn.execute(text("""
            INSERT INTO embedding_models (name, dimension, description, is_default, is_active)
            VALUES ('bge-m3', 1024, 'BGE-M3 embedding model', true, true)
            ON CONFLICT (name) DO UPDATE SET
                is_default = true,
                is_active = true
        """))

        # Create search outbox table
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS search_outbox (
                id BIGSERIAL PRIMARY KEY,
                document_id UUID NOT NULL,
                op VARCHAR(10) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                last_error VARCHAR(255),
                attempts INTEGER DEFAULT 0,
                next_retry_at TIMESTAMP WITH TIME ZONE,
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS embedding_models (
                name TEXT PRIMARY KEY,
                dimension INTEGER NOT NULL,
                description TEXT,
                is_default BOOLEAN DEFAULT false
            )
        """))

        # Seed default embedding model (required by IndexRequest)
        await conn.execute(
            text("""
                INSERT INTO embedding_models (name, dimension, description, is_default)
                VALUES (:name, :dimension, :description, :is_default)
            """),
            {
                "name": "bge-m3-v1",
                "dimension": 1024,
                "description": "BGE-M3-1024 embedding model",
                "is_default": True,
            },
        )

    yield engine

    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="function")
async def session(engine):
    """Function-scoped asyncpg session."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s


@pytest.fixture(scope="function")
async def contract_qdrant():
    """Function-scoped Qdrant container session."""
    from testcontainers.community.qdrant import QdrantContainer

    with QdrantContainer() as qdrant_container:
        client = qdrant_container.get_async_client()

        # Ensure documents collection exists (1024 cosine)
        await client.create_collection(
            collection_name="documents",
            vectors_config={"size": 1024, "distance": "Cosine"},
        )

        yield client


@pytest.fixture
def embedding_service_stub():
    """Stub embedding service that returns fixed vector."""
    from unittest.mock import MagicMock

    mock = MagicMock(spec=EmbeddingService)

    # Simple mock for embed
    async def mock_embed(text):
        return [0.1] * 1024

    mock.embed = mock_embed

    # Simple mock for embed_document - return basic structure
    class MockDocument:
        def __init__(self):
            self.text = "test content"
            self.chunk_idx = 0
            self.section_type = "test"

    class MockEmbedded:
        def __init__(self):
            self.chunks = [MockDocument()]
            self.embedding = [0.2] * 1024

    mock_embedded = MockEmbedded()

    async def mock_embed_document(*args, **kwargs):
        return mock_embedded

    mock.embed_document = mock_embed_document

    return mock


@pytest.fixture
def embedding_cache():
    """FakeRedis-based embedding cache."""
    from fakeredis.aioredis import FakeRedis

    return EmbeddingCache(FakeRedis())


@pytest.fixture
def qdrant_service(contract_qdrant):
    """Qdrant service with contract test client."""
    return QdrantService(client=contract_qdrant, collection_name="documents")
