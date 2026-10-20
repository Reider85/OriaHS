"""A/B testing framework for search experiments (ARCHITECT §11.3, PR-03).

``ABTestingService`` provides:

- Sticky user distribution to experiment variants (stateless consistent
  hashing — ``sha256(user_id:experiment_id) % sticky_hash_buckets`` mapped
  through cumulative weights, so the same user always lands in the same
  variant of the same experiment without server-side assignment storage);
- Experiment lifecycle: create (parallel budget + duplicate guard), active
  lookup with a short process-local TTL cache, stop with winner;
- Sequential hypothesis testing and per-variant daily metrics delegated to
  ``ABExperimentQueries`` (alpha-spending, ARCHITECT §11.4).

``assign_variant`` is pure — ``ab_assignments_total`` is incremented by the
search orchestrator once per search assignment; the click path recomputes the
variant without double-counting.
"""

import hashlib
import logging
import time
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ABTestingConfig
from app.db.models.ab_experiment import ABExperiment
from app.db.queries.ab_experiments import ABExperimentQueries
from app.observability import metrics

logger = logging.getLogger(__name__)

_ZERO_METRICS = {"count": 0.0, "ctr": 0.0, "mrr": 0.0, "no_click_rate": 0.0, "latency_p95": 0.0}


class ABTestingService:
    """A/B experiment management and sticky variant assignment."""

    # Process-local TTL cache of *active* experiments. Class-level on purpose:
    # the service is instantiated per request, and the search hot path must
    # not re-read the same experiment row on every query. Invalidation on
    # stop keeps a live experiment from being served past its end (cron-driven
    # stops in another process may lag up to one TTL window).
    _cache: dict[str, tuple[float, ABExperiment]] = {}
    _cache_ttl_seconds = 60.0

    def __init__(self, session: AsyncSession, config: ABTestingConfig) -> None:
        self._session = session
        self._config = config
        self._queries = ABExperimentQueries(config)

    # ------------------------------------------------------------------
    # Sticky distribution
    # ------------------------------------------------------------------

    def assign_variant(
        self,
        user_id: UUID | str,
        experiment_id: str,
        variants: list[str],
        weights: list[float] | dict[str, float] | None = None,
    ) -> str:
        """Assign a user to a variant via sticky consistent hashing.

        ``weights`` may be a list aligned with ``variants`` or a mapping
        name -> share (the JSONB storage shape). Equal split when omitted.
        """
        if not variants:
            raise ValueError("variants must not be empty")

        if isinstance(weights, dict):
            w = [float(weights.get(name, 0.0)) for name in variants]
        elif weights is None:
            w = [1.0] * len(variants)
        else:
            w = [float(x) for x in weights]
            if len(w) != len(variants):
                logger.warning(
                    "Weight/variant length mismatch, falling back to equal weights",
                    extra={
                        "experiment_id": experiment_id,
                        "variants": len(variants),
                        "weights": len(w),
                    },
                )
                w = [1.0] * len(variants)

        total = sum(w)
        if total <= 0:
            w = [1.0] * len(variants)
            total = float(len(variants))
        shares = [weight / total for weight in w]

        hash_input = f"{user_id}:{experiment_id}"
        bucket = (
            int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
            % self._config.sticky_hash_buckets
        )

        cumulative = 0.0
        for share, variant in zip(shares, variants, strict=True):
            cumulative += share
            if bucket < cumulative * self._config.sticky_hash_buckets:
                return variant
        return variants[-1]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create_experiment(
        self,
        *,
        experiment_id: str,
        description: str,
        variants: list[str] | dict[str, Any],
        weights: list[float] | None = None,
        start_date: date | None = None,
    ) -> None:
        """Create a new experiment (``status='active'``).

        Raises:
            ValueError: duplicate id, <2 variants, weight mismatch, or when
                the ``max_parallel_experiments`` budget is exhausted.
            IntegrityError: race on duplicate id (same unique constraint).
        """
        if isinstance(variants, dict):
            variants_dict = dict(variants)
        else:
            if len(variants) < 2:
                raise ValueError("at least two variants are required")
            if len(set(variants)) != len(variants):
                raise ValueError("variant names must be unique")
            variants_dict = {name: {} for name in variants}
        if len(variants_dict) < 2:
            raise ValueError("at least two variants are required")

        if weights is not None:
            if len(weights) != len(variants_dict):
                raise ValueError("weights length must match the number of variants")
            if sum(weights) <= 0:
                raise ValueError("weights must sum to a positive value")
            weights_dict: dict[str, float] | None = dict(
                zip(variants_dict.keys(), weights, strict=True)
            )
        else:
            weights_dict = None

        active = await self._queries.count_active(self._session)
        if active >= self._config.max_parallel_experiments:
            raise ValueError(
                f"Maximum parallel experiments ({self._config.max_parallel_experiments}) reached"
            )

        try:
            await self._queries.create_experiment(
                self._session,
                experiment_id=experiment_id,
                description=description,
                variants=variants_dict,
                weights=weights_dict,
                start_date=start_date,
            )
            await self._session.commit()
        except IntegrityError as exc:
            raise ValueError(f"Experiment '{experiment_id}' already exists") from exc

        metrics.ab_experiments_active.inc()
        logger.info(
            "Experiment created",
            extra={"experiment_id": experiment_id, "variants": list(variants_dict)},
        )

    async def get_experiment(self, experiment_id: str) -> ABExperiment | None:
        """Return the experiment when it is active, else ``None`` (TTL-cached)."""
        cls = type(self)
        cached = cls._cache.get(experiment_id)
        if cached is not None and time.monotonic() - cached[0] < cls._cache_ttl_seconds:
            return cached[1]

        experiment = await self._queries.get_by_id(self._session, experiment_id)
        if experiment is None or experiment.status != "active":
            return None
        cls._cache[experiment_id] = (time.monotonic(), experiment)
        return experiment

    async def get_active_experiments(self) -> list[ABExperiment]:
        """List all currently active experiments."""
        return await self._queries.get_active(self._session)

    async def compute_daily_metrics(
        self, experiment_id: str, target_date: date
    ) -> dict[str, dict[str, float]]:
        """Per-variant daily metrics; every declared variant is present.

        Raises:
            ValueError: experiment not found (route maps it to HTTP 404).
        """
        experiment = await self._queries.get_by_id(self._session, experiment_id)
        if experiment is None:
            raise ValueError(f"Experiment '{experiment_id}' not found")

        metrics_by_variant = await self._queries.compute_daily_metrics(
            self._session, experiment_id, target_date
        )
        for name in experiment.variants:
            metrics_by_variant.setdefault(name, dict(_ZERO_METRICS))
        return metrics_by_variant

    async def check_significance(self, experiment_id: str) -> dict[str, Any]:
        """Sequential significance result (never raises)."""
        try:
            return await self._queries.check_significance(self._session, experiment_id)
        except Exception as exc:  # noqa: BLE001 — stats path must never raise
            logger.warning(
                "Significance check failed, reporting insufficient samples",
                extra={"experiment_id": experiment_id, "error": str(exc)},
            )
            return {
                "significant": False,
                "winning_variant": None,
                "p_value": 1.0,
                "samples_per_variant": {},
                "insufficient_samples": True,
            }

    async def stop_experiment(self, experiment_id: str, winning_variant: str) -> None:
        """Stop an active experiment and record the winning variant.

        Raises:
            ValueError: unknown experiment, already stopped, or unknown
                winning variant name.
        """
        experiment = await self._queries.get_by_id(self._session, experiment_id)
        if experiment is None:
            raise ValueError(f"Experiment '{experiment_id}' not found")
        if experiment.status != "active":
            raise ValueError(f"Experiment '{experiment_id}' is not active")
        if winning_variant not in experiment.variants:
            raise ValueError(
                f"Unknown variant '{winning_variant}' for experiment '{experiment_id}'"
            )

        updated = await self._queries.update_status(
            self._session,
            experiment_id=experiment_id,
            status="stopped",
            winning_variant=winning_variant,
        )
        if not updated:
            raise ValueError(f"Experiment '{experiment_id}' not found")
        await self._session.commit()

        metrics.ab_experiments_active.dec()
        type(self)._cache.pop(experiment_id, None)
        logger.info(
            "Experiment stopped",
            extra={"experiment_id": experiment_id, "winning_variant": winning_variant},
        )
