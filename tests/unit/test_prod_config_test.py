"""Tests for Production-Ready configuration (PR-00).

Tests all new config sections and feature flags from PROD-PROMPTS.md.
Modelled after test_critical_config_test.py.
"""

import os

import pytest
from pydantic import ValidationError

from app.config import (
    AdaptiveKConfig,
    AppConfig,
    ABTestingConfig,
    ChaosConfig,
    FeatureFlags,
    IncrementalFacetsConfig,
    MigrationConfig,
    NegativeCacheConfig,
    PersonalizationConfig,
    ShadowTrafficConfig,
    TracingConfig,
)


class TestPersonalizationConfig:
    """Test PersonalizationConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = PersonalizationConfig()

        assert config.ema_alpha_new == 0.5
        assert config.ema_alpha_established == 0.05
        assert config.new_user_click_threshold == 10
        assert config.profile_weight == 0.3
        assert config.fused_weight == 0.7
        assert config.cold_start_enabled is True
        assert config.profile_dimension == 1024
        assert config.gdpr_delete_endpoint_enabled is True

    def test_env_override(self):
        """Test environment variable override."""
        with pytest.raises(ValidationError):
            PersonalizationConfig(ema_alpha_new=2.0)  # Outside bounds

        with pytest.raises(ValidationError):
            PersonalizationConfig(profile_weight=1.5)  # Outside bounds


class TestABTestingConfig:
    """Test ABTestingConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = ABTestingConfig()

        assert config.sticky_hash_buckets == 1000
        assert config.sequential_testing_enabled is True
        assert config.significance_level == 0.05
        assert config.min_samples_per_variant == 100
        assert config.max_parallel_experiments == 3

    def test_env_override(self):
        """Test environment variable override."""
        with pytest.raises(ValidationError):
            ABTestingConfig(sticky_hash_buckets=-1)  # Invalid


class TestTracingConfig:
    """Test TracingConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = TracingConfig()

        assert config.otel_endpoint == "http://otel-collector:4317"
        assert config.service_name == "oriahs"
        assert config.export_interval_ms == 5000
        assert config.sample_rate == 1.0

    def test_env_override(self):
        """Test environment variable override."""
        os.environ["TRACING_OTEL_ENDPOINT"] = "http://custom:1234"
        config = TracingConfig()
        assert config.otel_endpoint == "http://custom:1234"
        del os.environ["TRACING_OTEL_ENDPOINT"]


class TestMigrationConfig:
    """Test MigrationConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = MigrationConfig()

        assert config.backfill_batch_size == 1000
        assert config.backfill_speed_doc_per_hour == 50000
        assert config.canary_percentage == 0.01
        assert config.canary_min_similarity == 0.6
        assert config.canary_max_similarity == 0.95
        assert config.snapshot_retention_days == 30
        assert config.snapshot_s3_bucket == "oriahs-qdrant-snapshots"
        assert config.rollback_timeout_minutes == 30


class TestShadowTrafficConfig:
    """Test ShadowTrafficConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = ShadowTrafficConfig()

        assert config.enabled is False
        assert config.strategies == []
        assert config.log_variant is True


class TestChaosConfig:
    """Test ChaosConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = ChaosConfig()

        assert config.schedule_cron == "0 6 * * 0"
        assert config.environment == "staging"
        assert config.qdrant_kill_seconds == 60
        assert config.reranker_delay_ms == 1000
        assert config.redis_fill_percentage == 0.95


class TestAdaptiveKConfig:
    """Test AdaptiveKConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = AdaptiveKConfig()

        assert config.small_tenant_doc_threshold == 10000
        assert config.large_tenant_doc_threshold == 1000000
        assert config.short_query_len_threshold == 5
        assert config.long_query_len_threshold == 20
        assert config.k_small == 30
        assert config.k_large == 100


class TestIncrementalFacetsConfig:
    """Test IncrementalFacetsConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = IncrementalFacetsConfig()

        assert config.cache_ttl_seconds == 600
        assert config.cache_key_prefix == "facets"


class TestNegativeCacheConfig:
    """Test NegativeCacheConfig default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = NegativeCacheConfig()

        assert config.null_ttl_seconds == 60
        assert config.enabled is True


class TestFeatureFlags:
    """Test FeatureFlags default values and env override."""

    def test_default_values(self):
        """Test default values match PROD-PROMPTS.md."""
        config = FeatureFlags()

        # Critical phase flags
        assert config.vector_search_enabled is True
        assert config.rerank_enabled is True
        assert config.weighted_fusion_enabled is True
        assert config.pushdown_enabled is True
        assert config.throttle_enabled is True

        # Production-Ready phase flags
        assert config.personalization_enabled is True
        assert config.ab_testing_enabled is True
        assert config.diversify_enabled is True
        assert config.context_personalization_enabled is True
        assert config.tracing_enabled is True
        assert config.migration_framework_enabled is True
        assert config.shadow_traffic_enabled is False  # Default off
        assert config.adaptive_k_enabled is True
        assert config.incremental_facets_enabled is True
        assert config.negative_cache_enabled is True
        assert config.asymmetric_indexing_enabled is True

    def test_env_override(self):
        """Test environment variable override."""
        os.environ["SHADOW_TRAFFIC_ENABLED"] = "true"
        config = FeatureFlags()
        assert config.shadow_traffic_enabled is True
        del os.environ["SHADOW_TRAFFIC_ENABLED"]


class TestAppConfigIntegration:
    """Test AppConfig integration with all new sections."""

    def test_all_sections_present(self):
        """Test all Production-Ready config sections are present in AppConfig."""
        config = AppConfig()

        # Check new sections exist
        assert hasattr(config, 'personalization')
        assert hasattr(config, 'ab_testing')
        assert hasattr(config, 'tracing')
        assert hasattr(config, 'migration')
        assert hasattr(config, 'shadow_traffic')
        assert hasattr(config, 'chaos')
        assert hasattr(config, 'adaptive_k')
        assert hasattr(config, 'incremental_facets')
        assert hasattr(config, 'negative_cache')
        assert hasattr(config, 'feature_flags')

        # Check they're the correct types
        assert isinstance(config.personalization, PersonalizationConfig)
        assert isinstance(config.ab_testing, ABTestingConfig)
        assert isinstance(config.tracing, TracingConfig)
        assert isinstance(config.migration, MigrationConfig)
        assert isinstance(config.shadow_traffic, ShadowTrafficConfig)
        assert isinstance(config.chaos, ChaosConfig)
        assert isinstance(config.adaptive_k, AdaptiveKConfig)
        assert isinstance(config.incremental_facets, IncrementalFacetsConfig)
        assert isinstance(config.negative_cache, NegativeCacheConfig)
        assert isinstance(config.feature_flags, FeatureFlags)

    def test_cached_settings_identity(self):
        """Test that get_settings() returns the same instance (cached)."""
        config1 = get_settings()
        config2 = get_settings()
        assert config1 is config2


def get_settings():
    """Import AppConfig with proper path for testing."""
    from app.config import get_settings
    return get_settings()