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
    hf_model: str = "BAAI/bge-m3"  # HuggingFace model ID
    expected_dimension: int | None = None  # Expected embedding dimension (default: 1024)
    batch_size: int = Field(default=32, ge=1)
    max_length: int = Field(default=512, ge=1)
    cache_ttl_seconds: int = Field(default=2592000, ge=0)
    device: str = "auto"  # "auto" | "cuda" | "cpu"
    # Migration support for arbitrary model names
    hf_model_v2: str = "BAAI/bge-m3"  # HuggingFace model ID for v2 migration


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
    # Total metrics: 29 base + 5 migration + 3 asymmetric indexing = 37 total metrics


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

    model_config = SettingsConfigDict(
        env_prefix="CIRCUIT_BREAKER_", env_file=".env", extra="ignore"
    )

    error_rate_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    latency_p95_threshold_ms: int = Field(default=500, ge=1)
    # Окно и cooldown - длительности, а не счётчики: sub-second значения
    # (0.1s) нужны для быстрых тестов и нереальны для прод-конфига, но
    # запрещать их int-типом было ошибкой.
    window_seconds: float = Field(default=60.0, gt=0.0)
    cooldown_seconds: float = Field(default=60.0, ge=0.0)


class EvalConfig(BaseSettings):
    """Offline evaluation settings (ARCHITECT §11.5, §17.2)."""

    model_config = SettingsConfigDict(env_prefix="EVAL_", env_file=".env", extra="ignore")

    dataset_path: str = "eval/datasets/baseline_v1.jsonl"
    baselines_dir: str = "eval/baselines/"
    recall_regression_threshold: float = Field(default=0.02, ge=0.0)
    ndcg_regression_threshold: float = Field(default=0.01, ge=0.0)
    cron: str = "0 2 * * *"  # nightly at 02:00 UTC
    block_release: bool = Field(True)


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


class PersonalizationConfig(BaseSettings):
    """Personalization configuration (EMA profile vectors, cold-start, GDPR)."""

    model_config = SettingsConfigDict(
        env_prefix="PERSONALIZATION_", env_file=".env", extra="ignore"
    )

    ema_alpha_new: float = Field(default=0.5, ge=0.0, le=1.0)
    ema_alpha_established: float = Field(default=0.05, ge=0.0, le=1.0)
    new_user_click_threshold: int = Field(default=10, ge=1)
    profile_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    fused_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    cold_start_enabled: bool = True
    profile_dimension: int = 1024
    gdpr_delete_endpoint_enabled: bool = True

    # Contextual personalization settings (PR-05)
    session_profile_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    context_profile_alpha: float = Field(default=0.1, ge=0.0, le=1.0)
    session_profile_ttl_hours: int = Field(default=24, ge=1)
    context_timezone: str = "UTC"


class ABTestingConfig(BaseSettings):
    """A/B testing configuration (sticky distribution, sequential testing)."""

    model_config = SettingsConfigDict(env_prefix="AB_TESTING_", env_file=".env", extra="ignore")

    sticky_hash_buckets: int = Field(default=1000, gt=0)
    sequential_testing_enabled: bool = True
    significance_level: float = Field(default=0.05, gt=0.0, lt=1.0)
    min_samples_per_variant: int = Field(default=100, ge=1)
    max_parallel_experiments: int = Field(default=3, ge=1)


class TracingConfig(BaseSettings):
    """Distributed tracing OpenTelemetry configuration."""

    model_config = SettingsConfigDict(env_prefix="TRACING_", env_file=".env", extra="ignore")

    otel_endpoint: str = "http://otel-collector:4317"
    service_name: str = "oriahs"
    export_interval_ms: int = 5000
    sample_rate: float = 1.0


class MigrationConfig(BaseSettings):
    """Embedding model migration framework configuration (blue-green, backfill, canary)."""

    model_config = SettingsConfigDict(env_prefix="MIGRATION_", env_file=".env", extra="ignore")

    backfill_batch_size: int = 1000
    backfill_speed_doc_per_hour: int = 50000
    canary_percentage: float = 0.01
    canary_min_similarity: float = 0.6
    canary_max_similarity: float = 0.95
    snapshot_retention_days: int = 30
    snapshot_s3_bucket: str = "oriahs-qdrant-snapshots"
    rollback_timeout_minutes: int = 30

    # S3 configuration for snapshots
    snapshot_s3_endpoint_url: str = "http://minio:9000"
    snapshot_s3_access_key: str = "minioadmin"
    snapshot_s3_secret_key: str = "minioadmin"

    # Search collection override for migration
    search_collection: str | None = None

    # Collection naming
    collection_suffix: str = "_v2"

    # Canary and backfill limits
    max_canary_docs: int = 5000

    # Cleanup configuration
    stable_days: int = 7


class SearchEventsConfig(BaseSettings):
    """Search events partitioning and S3 archive configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SEARCH_EVENTS_", env_file=".env", extra="ignore"
    )

    # S3/MinIO configuration for Parquet archive
    s3_endpoint_url: str = "http://minio:9000"
    s3_bucket: str = "oriahs-search-events-archive"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"

    # Partitioning configuration
    retention_days: int = 90
    partition_ahead_days: int = 7
    migration_partition_days: int = 30

    # PII protection
    hash_query_text: bool = False

    # Performance
    click_statement_timeout_ms: int = 100


class ShadowTrafficConfig(BaseSettings):
    """Shadow traffic configuration (parallel strategy evaluation)."""

    model_config = SettingsConfigDict(env_prefix="SHADOW_", env_file=".env", extra="ignore")

    enabled: bool = False
    strategies: list[str] = []
    log_variant: bool = True
    experiment_id: str | None = None
    sample_rate: float = Field(1.0, ge=0.0, le=1.0)


class ChaosConfig(BaseSettings):
    """Chaos engineering configuration (weekly experiments)."""

    model_config = SettingsConfigDict(env_prefix="CHAOS_", env_file=".env", extra="ignore")

    schedule_cron: str = "0 6 * * 0"
    environment: str = "staging"
    qdrant_kill_seconds: int = 60
    reranker_delay_ms: int = 1000
    redis_fill_percentage: float = 0.95


class AdaptiveKConfig(BaseSettings):
    """Adaptive K configuration per tenant statistics."""

    model_config = SettingsConfigDict(env_prefix="ADAPTIVE_K_", env_file=".env", extra="ignore")

    small_tenant_doc_threshold: int = 10000
    large_tenant_doc_threshold: int = 1000000
    short_query_len_threshold: int = 5
    long_query_len_threshold: int = 20
    k_small: int = 30
    k_large: int = 100
    load_timeout_ms: int = 100


class IncrementalFacetsConfig(BaseSettings):
    """Incremental facets configuration (Redis cache)."""

    model_config = SettingsConfigDict(
        env_prefix="INCREMENTAL_FACETS_", env_file=".env", extra="ignore"
    )

    cache_ttl_seconds: int = 600
    cache_key_prefix: str = "facets"


class NegativeCacheConfig(BaseSettings):
    """Negative cache configuration for embeddings."""

    model_config = SettingsConfigDict(env_prefix="NEGATIVE_CACHE_", env_file=".env", extra="ignore")

    null_ttl_seconds: int = 60
    enabled: bool = True


class LangIdConfig(BaseSettings):
    """Language identification configuration (ARCHITECT §9.1, PR-12)."""

    model_config = SettingsConfigDict(env_prefix="LANGID_", env_file=".env", extra="ignore")

    fasttext_confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    """Confidence threshold below which fallback to tenant default language."""

    model_path: str | None = None
    """Path to FastText language model (lid.176.ftz). If None, auto-download to cache."""

    auto_download: bool = True
    """Enable auto-download of lid.176.ftz model to cache directory."""

    fallback_language: str = "en"
    """Fallback language when detection confidence is below threshold."""


class MMRConfig(BaseSettings):
    """MMR diversification configuration (ARCHITECT §10.5, PR-04)."""

    model_config = SettingsConfigDict(env_prefix="MMR_", env_file=".env", extra="ignore")

    default_lambda: float = Field(default=0.7, ge=0.0, le=1.0)
    """Default λ for MMR balancing (relevance vs diversity)."""

    adaptive_enabled: bool = True
    """Enable adaptive λ adjustment based on diversity_score."""

    diversity_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    """Minimum diversity_score; adaptive λ triggers if below."""

    lambda_step: float = Field(default=0.1, gt=0.0, le=1.0)
    """How much to decrease λ per adaptive iteration."""

    min_lambda: float = Field(default=0.5, ge=0.0, le=1.0)
    """Floor for λ (max 1-λ = 0.5 diversity weight)."""


class FeatureFlags(BaseSettings):
    """Feature flags for Critical and Production-Ready phase components (ARCHITECT §17.2)."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Critical phase flags
    vector_search_enabled: bool = True
    rerank_enabled: bool = True
    weighted_fusion_enabled: bool = True
    pushdown_enabled: bool = True
    adaptive_k_enabled: bool = True
    throttle_enabled: bool = True

    # Production-Ready phase flags
    personalization_enabled: bool = True
    ab_testing_enabled: bool = True
    diversify_enabled: bool = True
    context_personalization_enabled: bool = True
    tracing_enabled: bool = True
    migration_framework_enabled: bool = True
    shadow_traffic_enabled: bool = False
    adaptive_k_enabled: bool = True
    incremental_facets_enabled: bool = True
    negative_cache_enabled: bool = True
    asymmetric_indexing_enabled: bool = True
    mixed_language_enabled: bool = True


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

    # Production-Ready phase configs
    langid: Annotated[LangIdConfig, Field(default_factory=LangIdConfig)]
    personalization: Annotated[PersonalizationConfig, Field(default_factory=PersonalizationConfig)]
    ab_testing: Annotated[ABTestingConfig, Field(default_factory=ABTestingConfig)]
    tracing: Annotated[TracingConfig, Field(default_factory=TracingConfig)]
    migration: Annotated[MigrationConfig, Field(default_factory=MigrationConfig)]
    search_events: Annotated[SearchEventsConfig, Field(default_factory=SearchEventsConfig)]
    shadow_traffic: Annotated[ShadowTrafficConfig, Field(default_factory=ShadowTrafficConfig)]
    chaos: Annotated[ChaosConfig, Field(default_factory=ChaosConfig)]
    adaptive_k: Annotated[AdaptiveKConfig, Field(default_factory=AdaptiveKConfig)]
    incremental_facets: Annotated[
        IncrementalFacetsConfig, Field(default_factory=IncrementalFacetsConfig)
    ]
    negative_cache: Annotated[NegativeCacheConfig, Field(default_factory=NegativeCacheConfig)]
    mmr: Annotated[MMRConfig, Field(default_factory=MMRConfig)]
    feature_flags: Annotated[FeatureFlags, Field(default_factory=FeatureFlags)]


@lru_cache
def get_settings() -> AppConfig:
    """Cached settings singleton (re-read only on process restart)."""
    return AppConfig()


settings = get_settings()
