"""Daily A/B testing metrics computation and significance checking (PR-03).

Cron job that runs daily to:

1. Compute per-variant metrics for every active experiment (logged; the
   Prometheus time series are the metrics themselves);
2. Check statistical significance with the alpha-spending sequential test
   (ARCHITECT §11.4) — ``ABExperimentQueries.check_significance`` increments
   ``ab_significance_checked_total`` per variant;
3. Stop experiments that reached significance, recording the winner;
4. Self-heal the ``ab_experiments_active`` gauge from the actual DB count
   (the gauge is incremented/decremented in the API process; a restart or an
   out-of-band stop would otherwise leave it drifting).
"""

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import get_settings
from app.db.queries.ab_experiments import ABExperimentQueries
from app.db.session import async_session_factory
from app.observability import metrics

logger = logging.getLogger(__name__)


async def run_ab_metrics() -> None:
    """Run daily A/B metrics computation and significance checking."""
    logger.info("Starting daily A/B metrics computation")

    try:
        async with async_session_factory() as session:
            app_config = get_settings()
            if not app_config.feature_flags.ab_testing_enabled:
                logger.info("A/B testing is disabled, skipping metrics computation")
                return

            ab_queries = ABExperimentQueries(app_config.ab_testing)
            active_experiments = await ab_queries.get_active(session)

            if not active_experiments:
                logger.info("No active experiments to process")
                metrics.ab_experiments_active.set(0)
                return

            logger.info(f"Processing {len(active_experiments)} active experiments")

            for experiment in active_experiments:
                try:
                    experiment_id = experiment.experiment_id
                    yesterday = datetime.now().date() - timedelta(days=1)
                    metrics_data = await ab_queries.compute_daily_metrics(
                        session, experiment_id, yesterday
                    )
                    logger.info(
                        "Experiment metrics computed",
                        extra={
                            "experiment_id": experiment_id,
                            "date": str(yesterday),
                            "variants": list(metrics_data.keys()),
                        },
                    )

                    significance_result = await ab_queries.check_significance(
                        session, experiment_id
                    )
                    logger.info(
                        "Significance check completed",
                        extra={
                            "experiment_id": experiment_id,
                            "significant": significance_result["significant"],
                            "p_value": significance_result["p_value"],
                            "samples_per_variant": significance_result["samples_per_variant"],
                            "insufficient_samples": significance_result["insufficient_samples"],
                        },
                    )

                    if (
                        significance_result["significant"]
                        and not significance_result["insufficient_samples"]
                        and significance_result["winning_variant"]
                    ):
                        winner = significance_result["winning_variant"]
                        await ab_queries.update_status(
                            session,
                            experiment_id=experiment_id,
                            status="stopped",
                            winning_variant=winner,
                        )
                        await session.commit()
                        metrics.ab_significance_checked_total.labels(
                            experiment_id=experiment_id,
                            variant=winner,
                            result="stopped",
                        ).inc()
                        logger.info(
                            "Experiment stopped due to significant result",
                            extra={
                                "experiment_id": experiment_id,
                                "winning_variant": winner,
                                "p_value": significance_result["p_value"],
                            },
                        )
                except Exception as exc:
                    logger.error(
                        "Failed to process experiment",
                        extra={"experiment_id": experiment.experiment_id, "error": str(exc)},
                    )
                    continue

            metrics.ab_experiments_active.set(await ab_queries.count_active(session))
            logger.info("Daily A/B metrics computation completed successfully")

    except Exception as exc:
        logger.error("Daily A/B metrics computation failed", extra={"error": str(exc)})
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(run_ab_metrics())
