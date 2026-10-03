"""Tests for Critical phase configuration (C-00).

Verifies that all new config sections load correctly from environment
variables and have expected default values.
"""

from unittest.mock import patch

import pytest

from app.config import (
    AppConfig,
    CircuitBreakerConfig,
    EvalConfig,
    FeatureFlags,
    PushdownConfig,
    RerankerConfig,
    ThrottleConfig,
)


class TestRerankerConfig:
    """Test reranker configuration loading."""

    def test_default_values(self):
        """Test default config values."""
        config = RerankerConfig()

        assert config.model_name == "BAAI/bge-reranker-v2-m3"
        assert config.batch_size == 32
        assert config.max_length == 512
        assert config.device == "auto"
        assert config.mock_mode is False
        assert config.timeout_ms == 500
        assert config.speculative_top_n == 10
        assert config.speculative_enabled is True

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict("os.environ", {"RERANKER_MODEL_NAME": "test-model"}):
            config = RerankerConfig()
            assert config.model_name == "test-model"

    def test_mock_mode_true(self):
        """Test mock mode configuration."""
        with patch.dict("os.environ", {"RERANKER_MOCK_MODE": "true"}):
            config = RerankerConfig()
            assert config.mock_mode is True

    def test_device_detection(self):
        """Test device auto-detection logic."""
        # Note: actual device detection would require torch import
        # This just tests the config parsing
        with patch.dict("os.environ", {"RERANKER_DEVICE": "cuda"}):
            config = RerankerConfig()
            assert config.device == "cuda"


class TestCircuitBreakerConfig:
    """Test circuit breaker configuration loading."""

    def test_default_values(self):
        """Test default config values."""
        config = CircuitBreakerConfig()

        assert config.error_rate_threshold == 0.05
        assert config.latency_p95_threshold_ms == 500
        assert config.window_seconds == 60
        assert config.cooldown_seconds == 60

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(
            "os.environ",
            {
                "CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD": "0.1",
                "CIRCUIT_BREAKER_LATENCY_P95_THRESHOLD_MS": "1000",
            },
        ):
            config = CircuitBreakerConfig()
            assert config.error_rate_threshold == 0.1
            assert config.latency_p95_threshold_ms == 1000

    def test_validation_bounds(self):
        """Test value validation bounds."""
        with pytest.raises(ValueError):
            CircuitBreakerConfig(error_rate_threshold=1.5)  # > 1.0

        with pytest.raises(ValueError):
            CircuitBreakerConfig(error_rate_threshold=-0.1)  # < 0.0


class TestEvalConfig:
    """Test evaluation configuration loading."""

    def test_default_values(self):
        """Test default config values."""
        config = EvalConfig()

        assert config.dataset_path == "eval/datasets/baseline_v1.jsonl"
        assert config.baselines_dir == "eval/baselines/"
        assert config.recall_regression_threshold == 0.02
        assert config.ndcg_regression_threshold == 0.01
        assert config.cron == "0 2 * * *"

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(
            "os.environ",
            {
                "EVAL_DATASET_PATH": "custom/path/dataset.jsonl",
                "EVAL_BASELINES_DIR": "custom/baselines/",
                "EVAL_CRON": "0 3 * * *",
            },
        ):
            config = EvalConfig()
            assert config.dataset_path == "custom/path/dataset.jsonl"
            assert config.baselines_dir == "custom/baselines/"
            assert config.cron == "0 3 * * *"


class TestPushdownConfig:
    """Test push-down filter configuration loading."""

    def test_default_values(self):
        """Test default config values."""
        config = PushdownConfig()

        assert config.selectivity_threshold == 0.1
        assert config.max_candidate_ids == 5000

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(
            "os.environ",
            {"PUSHDOWN_SELECTIVITY_THRESHOLD": "0.2", "PUSHDOWN_MAX_CANDIDATE_IDS": "10000"},
        ):
            config = PushdownConfig()
            assert config.selectivity_threshold == 0.2
            assert config.max_candidate_ids == 10000


class TestThrottleConfig:
    """Test throttling configuration loading."""

    def test_default_values(self):
        """Test default config values."""
        config = ThrottleConfig()

        assert config.pending_warn_threshold == 50000
        assert config.pending_reindex_threshold == 100000

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(
            "os.environ",
            {
                "THROTTLE_PENDING_WARN_THRESHOLD": "75000",
                "THROTTLE_PENDING_REINDEX_THRESHOLD": "150000",
            },
        ):
            config = ThrottleConfig()
            assert config.pending_warn_threshold == 75000
            assert config.pending_reindex_threshold == 150000


class TestFeatureFlags:
    """Test feature flags configuration."""

    def test_default_values(self):
        """Test default config values."""
        config = FeatureFlags()

        assert config.vector_search_enabled is True
        assert config.rerank_enabled is True
        assert config.weighted_fusion_enabled is True
        assert config.pushdown_enabled is True
        assert config.throttle_enabled is True

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict(
            "os.environ",
            {
                "VECTOR_SEARCH_ENABLED": "false",
                "RERANK_ENABLED": "false",
                "WEIGHTED_FUSION_ENABLED": "false",
                "PUSHDOWN_ENABLED": "false",
                "THROTTLE_ENABLED": "false",
            },
        ):
            config = FeatureFlags()
            assert config.vector_search_enabled is False
            assert config.rerank_enabled is False
            assert config.weighted_fusion_enabled is False
            assert config.pushdown_enabled is False
            assert config.throttle_enabled is False


class TestAppConfigIntegration:
    """Test that all critical configs are included in main app config."""

    def test_critical_configs_included(self):
        """Test that all new config sections are included in AppConfig."""
        config = AppConfig()

        # Check that all new config sections are present
        assert hasattr(config, "reranker")
        assert hasattr(config, "circuit_breaker")
        assert hasattr(config, "eval")
        assert hasattr(config, "pushdown")
        assert hasattr(config, "throttle")
        assert hasattr(config, "feature_flags")

        # Check they are the correct types
        assert isinstance(config.reranker, RerankerConfig)
        assert isinstance(config.circuit_breaker, CircuitBreakerConfig)
        assert isinstance(config.eval, EvalConfig)
        assert isinstance(config.pushdown, PushdownConfig)
        assert isinstance(config.throttle, ThrottleConfig)
        assert isinstance(config.feature_flags, FeatureFlags)

    def test_cached_settings(self):
        """Test that settings are cached."""
        from app.config import get_settings

        settings1 = get_settings()
        settings2 = get_settings()

        # Should be the same instance due to lru_cache
        assert settings1 is settings2
