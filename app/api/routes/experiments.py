"""A/B testing experiment management routes (ARCHITECT §11.3, PR-03).

``POST /experiments`` / ``GET /experiments`` / ``GET /experiments/{id}/metrics``
/ ``POST /experiments/{id}/stop``. Every route is gated by the
``ab_testing_enabled`` feature flag (HTTP 403 when disabled) and shares the
per-request ``ABTestingService`` instance from ``deps.get_ab_testing_service``.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_ab_testing_service, get_app_config
from app.api.schemas import (
    ExperimentCreate,
    ExperimentMetricsResponse,
    StopExperimentRequest,
)
from app.config import AppConfig
from app.services.ab_testing import ABTestingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/experiments", tags=["experiments"])

ConfigDep = Annotated[AppConfig, Depends(get_app_config)]
ABDep = Annotated[ABTestingService, Depends(get_ab_testing_service)]


def _require_ab_enabled(app_config: AppConfig) -> None:
    """Reject the call when the feature flag is off (C-00 kill switch)."""
    if not app_config.feature_flags.ab_testing_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A/B testing is disabled via feature flag",
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    body: ExperimentCreate,
    app_config: ConfigDep,
    ab_service: ABDep,
) -> dict[str, str]:
    """Create a new A/B testing experiment.

    Raises:
        HTTP 409: duplicate experiment_id or ``max_parallel_experiments``
            budget exhausted (shape errors are rejected earlier as HTTP 422
            by ``ExperimentCreate`` validators).
    """
    _require_ab_enabled(app_config)

    try:
        await ab_service.create_experiment(
            experiment_id=body.experiment_id,
            description=body.description,
            variants=body.variants,
            weights=body.weights,
            start_date=body.start_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    logger.info(
        "Experiment created via API",
        extra={"experiment_id": body.experiment_id, "variants": body.variants},
    )
    return {"experiment_id": body.experiment_id}


@router.get("/{experiment_id}/metrics")
async def get_experiment_metrics(
    experiment_id: str,
    app_config: ConfigDep,
    ab_service: ABDep,
    target_date: Annotated[date | None, Query(alias="date")] = None,
) -> ExperimentMetricsResponse:
    """Get daily per-variant metrics (default: yesterday).

    Raises:
        HTTP 404: experiment not found.
    """
    _require_ab_enabled(app_config)

    day = target_date or (datetime.now().date() - timedelta(days=1))
    try:
        metrics_data = await ab_service.compute_daily_metrics(experiment_id, day)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ExperimentMetricsResponse(date=day, metrics=metrics_data)


@router.post("/{experiment_id}/stop", status_code=status.HTTP_200_OK)
async def stop_experiment(
    experiment_id: str,
    stop_request: StopExperimentRequest,
    app_config: ConfigDep,
    ab_service: ABDep,
) -> dict[str, str]:
    """Stop an experiment and record the winning variant.

    Raises:
        HTTP 404: unknown experiment, already stopped, or unknown variant.
    """
    _require_ab_enabled(app_config)

    try:
        await ab_service.stop_experiment(
            experiment_id=experiment_id,
            winning_variant=stop_request.winning_variant,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"experiment_id": experiment_id}


@router.get("")
async def list_active_experiments(
    app_config: ConfigDep,
    ab_service: ABDep,
) -> list[dict[str, Any]]:
    """List all currently active experiments with their variant names."""
    _require_ab_enabled(app_config)

    experiments = await ab_service.get_active_experiments()
    return [
        {
            "experiment_id": exp.experiment_id,
            "description": exp.description,
            "variants": list(exp.variants.keys()),
        }
        for exp in experiments
    ]
