"""Query helpers for the ``ab_experiments`` table (ARCHITECT §11.3, PR-03).

Experiment CRUD, daily per-variant metrics and sequential significance
testing for the A/B framework. Instantiated as ``ABExperimentQueries(config)``
so the significance math shares one ``ABTestingConfig`` with the service
(min samples, significance level, sequential-testing switch). Raw SQL in the
style of ``queries/search_events.py``.

Sequential testing uses alpha-spending (Bonferroni): a daily check on day
``k`` of the experiment tests at level ``alpha_k = alpha / k`` (divided by
the number of pairwise comparisons), so repeated peeking keeps the family-wise
error rate at ``alpha`` without mSPRT machinery.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from math import erfc, sqrt
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ABTestingConfig
from app.db.models.ab_experiment import ABExperiment
from app.observability import metrics

logger = logging.getLogger(__name__)


def _row_to_experiment(row: Any) -> ABExperiment:
    """Map a raw ``ab_experiments`` row onto the ORM model."""
    return ABExperiment(
        id=row.id,
        experiment_id=row.experiment_id,
        description=row.description,
        variants=row.variants,
        weights=row.weights,
        status=row.status,
        start_date=row.start_date,
        end_date=row.end_date,
        winning_variant=row.winning_variant,
        created_at=row.created_at,
    )


def _two_proportion_p_value(
    clicks_a: int, searches_a: int, clicks_b: int, searches_b: int
) -> float:
    """Two-sided pooled z-test p-value for the difference of two proportions.

    Returns ``1.0`` for degenerate inputs (no samples or zero variance).
    """
    if searches_a <= 0 or searches_b <= 0:
        return 1.0
    p_a = clicks_a / searches_a
    p_b = clicks_b / searches_b
    pooled = (clicks_a + clicks_b) / (searches_a + searches_b)
    se = sqrt(pooled * (1.0 - pooled) * (1.0 / searches_a + 1.0 / searches_b))
    if se <= 0.0:
        return 1.0
    z = abs(p_b - p_a) / se
    return erfc(z / sqrt(2.0))  # two-sided standard normal tail


def _insufficient(samples: dict[str, int]) -> dict[str, Any]:
    """Canonical ``insufficient samples`` significance result."""
    return {
        "significant": False,
        "winning_variant": None,
        "p_value": 1.0,
        "samples_per_variant": samples,
        "insufficient_samples": True,
    }


class ABExperimentQueries:
    """Raw-SQL data access for the A/B experiment lifecycle."""

    def __init__(self, config: ABTestingConfig) -> None:
        self._config = config

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ABExperimentQueries):
            return NotImplemented
        return self._config == other._config

    async def create_experiment(
        self,
        session: AsyncSession,
        *,
        experiment_id: str,
        description: str | None,
        variants: dict[str, Any],
        weights: dict[str, float] | None,
        start_date: date | None = None,
    ) -> None:
        """Register a new experiment (``status='active'``) via the ORM.

        ORM insert so the JSONB ``variants``/``weights`` payloads go through
        the JSONB type codec (asyncpg cannot bind raw dicts in text SQL).
        Raises ``IntegrityError`` on duplicate ``experiment_id`` — the
        service converts it to ``ValueError`` for the route's 409.
        """
        session.add(
            ABExperiment(
                experiment_id=experiment_id,
                description=description,
                variants=variants,
                weights=weights,
                start_date=start_date or date.today(),
            )
        )
        await session.flush()

    async def get_by_id(self, session: AsyncSession, experiment_id: str) -> ABExperiment | None:
        """Fetch an experiment by its public identifier, ``None`` if not found."""
        result = await session.execute(
            text("SELECT * FROM ab_experiments WHERE experiment_id = :experiment_id"),
            {"experiment_id": experiment_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return _row_to_experiment(row)

    async def get_active(self, session: AsyncSession) -> list[ABExperiment]:
        """List all experiments currently in ``status='active'``, newest first."""
        result = await session.execute(
            text("SELECT * FROM ab_experiments WHERE status = 'active' ORDER BY created_at DESC")
        )
        return [_row_to_experiment(row) for row in result.fetchall()]

    async def count_active(self, session: AsyncSession) -> int:
        """Number of active experiments (enforces ``max_parallel_experiments``)."""
        result = await session.execute(
            text("SELECT count(*) FROM ab_experiments WHERE status = 'active'")
        )
        return int(result.scalar_one())

    async def update_status(
        self,
        session: AsyncSession,
        *,
        experiment_id: str,
        status: str,
        winning_variant: str | None = None,
    ) -> bool:
        """Update an experiment's lifecycle status (stop sets ``end_date``).

        Returns ``True`` when a row was updated (experiment existed).
        """
        result = await session.execute(
            text(
                """
                UPDATE ab_experiments
                SET status = :status,
                    winning_variant = :winning,
                    end_date = CASE WHEN :status = 'stopped' THEN :end_date ELSE end_date END
                WHERE experiment_id = :experiment_id
                """
            ),
            {
                "experiment_id": experiment_id,
                "status": status,
                "winning": winning_variant,
                "end_date": date.today(),
            },
        )
        return cast(CursorResult[Any], result).rowcount > 0

    async def compute_daily_metrics(
        self, session: AsyncSession, experiment_id: str, target_date: date
    ) -> dict[str, dict[str, float]]:
        """Per-variant daily metrics from ``search_events`` (ARCHITECT §11.4).

        Returns ``{variant: {count, ctr, mrr, no_click_rate, latency_p95}}``
        for variants that had traffic on ``target_date``. Rows without a
        ``variant`` (events predating the experiment) are excluded.

        - ``count`` / ``ctr``: search rows vs click rows (clicks / searches)
        - ``mrr``: mean reciprocal click position
        - ``no_click_rate``: ``1 - ctr`` (per-row attribution proxy)
        - ``latency_p95``: 95th percentile of search latency
        """
        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        result = await session.execute(
            text(
                """
                SELECT variant,
                       count(*) FILTER (
                           WHERE clicked_doc_id IS NULL AND top_doc_ids IS NOT NULL
                       ) AS searches,
                       count(*) FILTER (WHERE clicked_doc_id IS NOT NULL) AS clicks,
                       avg(1.0 / position) FILTER (
                           WHERE position IS NOT NULL AND position > 0
                       ) AS mrr,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (
                           WHERE clicked_doc_id IS NULL AND latency_ms IS NOT NULL
                       ) AS latency_p95
                FROM search_events
                WHERE experiment_id = :experiment_id
                  AND variant IS NOT NULL
                  AND created_at >= :day_start
                  AND created_at < :day_end
                GROUP BY variant
                """
            ),
            {"experiment_id": experiment_id, "day_start": day_start, "day_end": day_end},
        )

        metrics_by_variant: dict[str, dict[str, float]] = {}
        for row in result.fetchall():
            searches = int(row.searches or 0)
            clicks = int(row.clicks or 0)
            ctr = clicks / searches if searches else 0.0
            metrics_by_variant[row.variant] = {
                "count": float(searches),
                "ctr": ctr,
                "mrr": float(row.mrr or 0.0),
                "no_click_rate": (1.0 - ctr) if searches else 0.0,
                "latency_p95": float(row.latency_p95 or 0.0),
            }
        return metrics_by_variant

    async def get_variant_samples(
        self,
        session: AsyncSession,
        experiment_id: str,
        *,
        since: date | None = None,
    ) -> dict[str, dict[str, int]]:
        """Lifetime ``{variant: {searches, clicks}}`` samples for significance.

        ``since`` bounds the window at the experiment's start date so events
        from unrelated periods never inflate the sample size.
        """
        params: dict[str, Any] = {"experiment_id": experiment_id}
        date_clause = ""
        if since is not None:
            date_clause = "AND created_at >= :since"
            params["since"] = datetime(since.year, since.month, since.day, tzinfo=UTC)
        result = await session.execute(
            text(
                f"""
                SELECT variant,
                       count(*) FILTER (
                           WHERE clicked_doc_id IS NULL AND top_doc_ids IS NOT NULL
                       ) AS searches,
                       count(*) FILTER (WHERE clicked_doc_id IS NOT NULL) AS clicks
                FROM search_events
                WHERE experiment_id = :experiment_id
                  AND variant IS NOT NULL
                  {date_clause}
                GROUP BY variant
                """
            ),
            params,
        )
        return {
            row.variant: {"searches": int(row.searches or 0), "clicks": int(row.clicks or 0)}
            for row in result.fetchall()
        }

    async def check_significance(self, session: AsyncSession, experiment_id: str) -> dict[str, Any]:
        """Sequential significance check for an experiment (ARCHITECT §11.4).

        Returns the full result dict::

            {"significant": bool, "winning_variant": str | None,
             "p_value": float, "samples_per_variant": {variant: searches},
             "insufficient_samples": bool}

        The first variant of the experiment definition is the control; every
        other variant is compared to it with a two-sided pooled z-test at the
        alpha-spending level ``alpha / k / comparisons``. Any DB or math
        error degrades to ``insufficient_samples`` (never raises) — a broken
        stats path must not take the cron or the API down.
        """
        try:
            experiment = await self.get_by_id(session, experiment_id)
            if experiment is None:
                return _insufficient({})

            variant_names = list(experiment.variants)
            raw_samples = await self.get_variant_samples(
                session, experiment_id, since=experiment.start_date
            )
            samples = {name: raw_samples.get(name, {}).get("searches", 0) for name in variant_names}
            if not variant_names:
                return _insufficient(samples)

            min_samples = self._config.min_samples_per_variant
            if any(count < min_samples for count in samples.values()):
                result = _insufficient(samples)
                self._record_significance_metrics(experiment_id, samples, result)
                return result

            # Alpha-spending: day k of the experiment, Bonferroni over
            # pairwise comparisons against the control (first variant).
            if experiment.start_date is not None:
                k = max((date.today() - experiment.start_date).days + 1, 1)
            else:
                k = 1
            comparisons = max(len(variant_names) - 1, 1)
            if self._config.sequential_testing_enabled:
                threshold = self._config.significance_level / k / comparisons
            else:
                threshold = self._config.significance_level / comparisons

            control = variant_names[0]
            control_searches = raw_samples.get(control, {}).get("searches", 0)
            control_clicks = raw_samples.get(control, {}).get("clicks", 0)

            p_values: dict[str, float] = {}
            ctrs: dict[str, float] = {}
            for name in variant_names:
                searches = raw_samples.get(name, {}).get("searches", 0)
                clicks = raw_samples.get(name, {}).get("clicks", 0)
                ctrs[name] = clicks / searches if searches else 0.0
                if name == control:
                    continue
                p_values[name] = _two_proportion_p_value(
                    control_clicks, control_searches, clicks, searches
                )

            ranked = sorted(variant_names, key=lambda name: ctrs[name], reverse=True)
            winner = ranked[0]
            if winner == control:
                significant = bool(p_values) and all(
                    p_values[name] < threshold for name in ranked[1:]
                )
            else:
                significant = p_values.get(winner, 1.0) < threshold
            p_value = min(p_values.values()) if p_values else 1.0

            result = {
                "significant": significant,
                "winning_variant": winner if significant else None,
                "p_value": p_value,
                "samples_per_variant": samples,
                "insufficient_samples": False,
            }
            self._record_significance_metrics(experiment_id, samples, result)
            return result
        except Exception as exc:  # noqa: BLE001 — stats path must never raise
            logger.warning(
                "Significance check failed, reporting insufficient samples",
                extra={"experiment_id": experiment_id, "error": str(exc)},
            )
            return _insufficient({})

    @staticmethod
    def _record_significance_metrics(
        experiment_id: str, samples: dict[str, int], result: dict[str, Any]
    ) -> None:
        """Increment ``ab_significance_checked_total`` per variant."""
        if result["insufficient_samples"]:
            label = "insufficient"
        elif result["significant"]:
            label = "significant"
        else:
            label = "not_significant"
        for name in samples:
            metrics.ab_significance_checked_total.labels(
                experiment_id=experiment_id, variant=name, result=label
            ).inc()
