"""Application configuration.

Source of truth: ARCHITECT.md v2.0 §17.2 (config.yaml reference).
All environment-dependent parameters live here — no hardcoding anywhere else.
Priority: OS environment > .env file.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PostgresConfig(BaseSettings):
    """PostgreSQL connection settings (documents, search_outbox, ...)."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", env_file=".env", extra="ignore")

    user: str = "postgres"
    password: str = "postgres"
    host: str = "localhost"
    port: int = 5432
    db: str = "orlahs"

    @property
    def dsn(self) -> str:
        """SQLAlchemy async DSN (only asyncpg)."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class QdrantConfig(BaseSettings):
    """Qdrant vector store settings."""

    model_config = SettingsConfigDict(env_prefix="QDRANT_", env_file=".env", extra="ignore")

    url: str = "http://localhost:6333"
    grpc_url: str = "grpc://localhost:6334"
    api_key: str | None = None
    collection: str = "documents"
    replication_factor: int = 1


class RedisConfig(BaseSettings):
    """Redis settings (embedding cache + Streams queue)."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    pool_size: int = 10

    @property
    def dsn(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class EmbeddingConfig(BaseSettings):
    """Embedding pipeline settings (ARCHITECT §5.1, §5.2, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", env_file=".env", extra="ignore")

    model_name: str = "bge-m3-v1"
    batch_size: int = Field(default=32, ge=1)
    max_length: int = Field(default=512, ge=1)
    cache_ttl_seconds: int = Field(default=2592000, ge=0)
    device: str = "auto"  # "auto" | "cuda" | "cpu"


class SearchConfig(BaseSettings):
    """Search pipeline settings (ARCHITECT §17.2)."""

    model_config = SettingsConfigDict(env_prefix="SEARCH_", env_file=".env", extra="ignore")

    k_lexical: int = 50
    k_vector: int = 50
    k_rerank: int = 50
    k_final: int = 20
    default_fusion: str = "rrf"
    rrf_k: int = 60
    weighted_alpha: float = 0.5


class ReconcilerConfig(BaseSettings):
    """Outbox reconciler settings (ARCHITECT §4.4, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="RECONCILER_", env_file=".env", extra="ignore")

    poll_interval_seconds: int = 30
    batch_size: int = 500
    max_attempts: int = 20
    base_backoff_seconds: int = 10
    max_backoff_seconds: int = 3600
    digest_interval_minutes: int = 60


class ObservabilityConfig(BaseSettings):
    """Observability settings (ARCHITECT §11, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    otel_endpoint: str = "http://otel-collector:4317"
    metrics_path: str = "/metrics"
    log_level: str = "INFO"


class AppConfig(BaseSettings):
    """Root settings bundle. Priority: OS env > .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OriaHS"
    environment: str = "dev"
    debug: bool = True
    log_level: str = "INFO"
    api_port: int = 8000

    postgres: Annotated[PostgresConfig, Field(default_factory=PostgresConfig)]
    qdrant: Annotated[QdrantConfig, Field(default_factory=QdrantConfig)]
    redis: Annotated[RedisConfig, Field(default_factory=RedisConfig)]
    embedding: Annotated[EmbeddingConfig, Field(default_factory=EmbeddingConfig)]
    search: Annotated[SearchConfig, Field(default_factory=SearchConfig)]
    reconciler: Annotated[ReconcilerConfig, Field(default_factory=ReconcilerConfig)]
    observability: Annotated[ObservabilityConfig, Field(default_factory=ObservabilityConfig)]


@lru_cache
def get_settings() -> AppConfig:
    """Cached settings singleton (re-read only on process restart)."""
    return AppConfig()


settings = get_settings()
