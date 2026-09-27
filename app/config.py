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
    parallelism: int = 10


class ObservabilityConfig(BaseSettings):
    """Observability settings (ARCHITECT §11, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    otel_endpoint: str = "http://otel-collector:4317"
    metrics_path: str = "/metrics"
    log_level: str = "INFO"


class RerankerConfig(BaseSettings):
    """Cross-encoder reranker settings (ARCHITECT §7.3, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="RERANKER_", env_file=".env", extra="ignore")

    model_name: str = "BAAI/bge-reranker-v2-m3"
    batch_size: int = Field(default=32, ge=1)
    max_length: int = Field(default=512, ge=1)
    device: str = "auto"  # "auto" | "cuda" | "cpu"
    mock_mode: bool = False
    timeout_ms: int = Field(default=500, ge=1)
    speculative_top_n: int = Field(default=10, ge=1)
    speculative_enabled: bool = True
    warmup: bool = False


class CircuitBreakerConfig(BaseSettings):
    """Circuit breaker settings for reranker (ARCHITECT §6.6, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="CIRCUIT_BREAKER_", env_file=".env", extra="ignore")

    error_rate_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    latency_p95_threshold_ms: int = Field(default=500, ge=1)
    window_seconds: int = Field(default=60, ge=1)
    cooldown_seconds: int = Field(default=60, ge=1)


class EvalConfig(BaseSettings):
    """Offline evaluation settings (ARCHITECT §11.5, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="EVAL_", env_file=".env", extra="ignore")

    dataset_path: str = "eval/datasets/baseline_v1.jsonl"
    baselines_dir: str = "eval/baselines/"
    recall_regression_threshold: float = Field(default=0.02, ge=0.0)
    ndcg_regression_threshold: float = Field(default=0.01, ge=0.0)
    cron: str = "0 2 * * *"  # nightly at 02:00 UTC


class PushdownConfig(BaseSettings):
    """Push-down filter settings (ARCHITECT §8.2, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="PUSHDOWN_", env_file=".env", extra="ignore")

    selectivity_threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    max_candidate_ids: int = Field(default=5000, ge=1)


class ThrottleConfig(BaseSettings):
    """Throttling settings (ARCHITECT §4.6, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="THROTTLE_", env_file=".env", extra="ignore")

    pending_warn_threshold: int = Field(default=50000, ge=1)
    pending_reindex_threshold: int = Field(default=100000, ge=1)


class FeatureFlags(BaseSettings):
    """Feature flags for Critical phase components (ARCHITECT §17.2)."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    vector_search_enabled: bool = True
    rerank_enabled: bool = True
    weighted_fusion_enabled: bool = True
    pushdown_enabled: bool = True


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
    reranker: Annotated[RerankerConfig, Field(default_factory=RerankerConfig)]
    circuit_breaker: Annotated[CircuitBreakerConfig, Field(default_factory=CircuitBreakerConfig)]
    eval: Annotated[EvalConfig, Field(default_factory=EvalConfig)]
    pushdown: Annotated[PushdownConfig, Field(default_factory=PushdownConfig)]
    throttle: Annotated[ThrottleConfig, Field(default_factory=ThrottleConfig)]
    feature_flags: Annotated[FeatureFlags, Field(default_factory=FeatureFlags)]


@lru_cache
def get_settings() -> AppConfig:
    """Cached settings singleton (re-read only on process restart)."""
    return AppConfig()


settings = get_settings()
