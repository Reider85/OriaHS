"""Unit tests for A/B testing framework (PR-03).

Tests sticky distribution, sequential testing, experiment management,
and variant assignment logic. No Docker required: sessions are AsyncMocks.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import ABTestingConfig
from app.db.queries.ab_experiments import ABExperimentQueries
from app.observability import metrics
from app.services.ab_testing import ABTestingService


class TestABTestingService:
    """Test A/B testing service functionality."""

    async def test_assign_variant_deterministic(self):
        """Same user/experiment always lands in the same variant."""
        service = ABTestingService(AsyncMock(), ABTestingConfig())

        user_id = uuid4()
        variants = ["A", "B"]

        variant1 = service.assign_variant(user_id, "test_exp", variants)
        variant2 = service.assign_variant(user_id, "test_exp", variants)
        variant3 = service.assign_variant(user_id, "test_exp", variants)

        assert variant1 == variant2 == variant3

    async def test_assign_variant_distribution_equal_weights(self):
        """Equal weights produce ~50/50 distribution over 1000 hashed users."""
        service = ABTestingService(AsyncMock(), ABTestingConfig())

        variant_counts = {"A": 0, "B": 0}
        for _ in range(1000):
            variant = service.assign_variant(uuid4(), "test_exp", ["A", "B"])
            variant_counts[variant] += 1

        assert abs(variant_counts["A"] - 500) < 50
        assert abs(variant_counts["B"] - 500) < 50

    async def test_assign_variant_distribution_unequal_weights(self):
        """[0.7, 0.3] weights produce a ~70/30 distribution."""
        service = ABTestingService(AsyncMock(), ABTestingConfig())

        variant_counts = {"A": 0, "B": 0}
        for _ in range(1000):
            variant = service.assign_variant(uuid4(), "test_exp", ["A", "B"], [0.7, 0.3])
            variant_counts[variant] += 1

        assert abs(variant_counts["A"] - 700) < 50
        assert abs(variant_counts["B"] - 300) < 50

    async def test_assign_variant_weights_dict(self):
        """JSONB-shaped dict weights are honoured by name."""
        service = ABTestingService(AsyncMock(), ABTestingConfig())

        variant_counts = {"A": 0, "B": 0}
        for _ in range(1000):
            variant = service.assign_variant(
                uuid4(), "dict_weights_exp", ["A", "B"], {"A": 0.8, "B": 0.2}
            )
            variant_counts[variant] += 1

        assert abs(variant_counts["A"] - 800) < 50
        assert abs(variant_counts["B"] - 200) < 50

    async def test_create_experiment_max_parallel(self):
        """Second create fails once max_parallel_experiments is exhausted."""
        config = ABTestingConfig(max_parallel_experiments=1)
        session = AsyncMock()
        count_zero = MagicMock()
        count_zero.scalar_one.return_value = 0
        count_one = MagicMock()
        count_one.scalar_one.return_value = 1
        # create #1: count_active(0) + INSERT; create #2: count_active(1) -> ValueError
        session.execute = AsyncMock(side_effect=[count_zero, MagicMock(), count_one])
        service = ABTestingService(session, config)

        await service.create_experiment(
            experiment_id="exp1",
            description="Test experiment 1",
            variants=["A", "B"],
            start_date=date.today(),
        )

        with pytest.raises(ValueError, match="Maximum parallel experiments"):
            await service.create_experiment(
                experiment_id="exp2",
                description="Test experiment 2",
                variants=["A", "B"],
                start_date=date.today(),
            )

    async def test_create_experiment_duplicate(self):
        """IntegrityError on duplicate id is mapped to ValueError."""
        config = ABTestingConfig()
        session = AsyncMock()
        count_zero = MagicMock()
        count_zero.scalar_one.return_value = 0
        session.execute = AsyncMock(side_effect=[count_zero])
        # The ORM insert path raises IntegrityError at flush()
        session.flush = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("duplicate")))
        service = ABTestingService(session, config)

        with pytest.raises(ValueError, match="already exists"):
            await service.create_experiment(
                experiment_id="dup",
                description="Duplicate experiment",
                variants=["A", "B"],
                start_date=date.today(),
            )

    async def test_create_experiment_rejects_single_variant(self):
        """A one-variant experiment is a ValueError."""
        service = ABTestingService(AsyncMock(), ABTestingConfig())

        with pytest.raises(ValueError, match="at least two variants"):
            await service.create_experiment(
                experiment_id="single",
                description="Only one arm",
                variants=["A"],
                start_date=date.today(),
            )

    async def test_check_significance_never_raises(self):
        """A broken stats path degrades to insufficient_samples."""
        config = ABTestingConfig(min_samples_per_variant=100)
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("no database")
        service = ABTestingService(session, config)

        result = await service.check_significance("nonexistent_exp")

        assert result["insufficient_samples"] is True
        assert result["significant"] is False

    async def test_check_significant_result_shape(self):
        """Significance result always carries the full contract dict."""
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("no database")
        service = ABTestingService(session, ABTestingConfig())

        result = await service.check_significance("nonexistent_exp")

        assert "significant" in result
        assert "winning_variant" in result
        assert "p_value" in result
        assert "samples_per_variant" in result
        assert "insufficient_samples" in result

    def test_ab_metrics_counters(self):
        """A/B Prometheus metrics exist and accept their label sets."""
        assert hasattr(metrics, "ab_experiments_active")
        assert hasattr(metrics, "ab_assignments_total")
        assert hasattr(metrics, "ab_significance_checked_total")

        metrics.ab_assignments_total.labels(experiment_id="test_exp", variant="A").inc()
        metrics.ab_significance_checked_total.labels(
            experiment_id="test_exp", variant="A", result="significant"
        ).inc()

    async def test_experiment_queries_integration(self):
        """Service wires ABExperimentQueries(config) with config equality."""
        config = ABTestingConfig()
        queries = ABExperimentQueries(config)
        service = ABTestingService(AsyncMock(), config)

        assert service._queries == queries
        assert hasattr(queries, "create_experiment")
        assert hasattr(queries, "get_by_id")
        assert hasattr(queries, "get_active")
        assert hasattr(queries, "compute_daily_metrics")
        assert hasattr(queries, "check_significance")
        assert hasattr(queries, "update_status")
