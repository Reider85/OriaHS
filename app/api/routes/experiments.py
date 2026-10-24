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
    ShadowComparisonResponse,
    ShadowExperimentCreate,
    StopExperimentRequest,
    VariantMetrics,
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


@router.post("/shadow", status_code=status.HTTP_201_CREATED)
async def create_shadow_experiment(
    body: ShadowExperimentCreate,
    app_config: ConfigDep,
    ab_service: ABDep,
) -> dict[str, str]:
    """Create a shadow traffic experiment for parallel strategy evaluation (PR-08).
    
    Creates an A/B experiment with variants 'control' and treatment strategies.
    Gated by both ab_testing_enabled and shadow_traffic_enabled feature flags.
    
    Raises:
        HTTP 409: duplicate experiment_id
        HTTP 422: invalid strategy tokens
    """
    # Require both flags to be enabled
    if not app_config.feature_flags.ab_testing_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A/B testing is disabled via feature flag",
        )
    if not app_config.feature_flags.shadow_traffic_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Shadow traffic is disabled via feature flag",
        )
    
    # Parse strategies and build variants dict
    variants: dict[str, dict[str, Any]] = {}
    
    # Control strategy (always empty params - uses production config)
    variants["control"] = {}
    
    # Treatment strategies with parsed parameters
    for i, strategy_str in enumerate(body.treatment_strategies):
        try:
            # Parse strategy string into parameters
            params: dict[str, Any] = {}
            for token in strategy_str.split("+"):
                token = token.strip()
                if token in ("rrf", "weighted"):
                    params["fusion"] = token
                elif token == "rerank":
                    params["rerank"] = True
                elif token == "personalize":
                    params["personalize"] = True
                elif token == "diversify":
                    params["diversify"] = True
                elif token.startswith("alpha:"):
                    try:
                        params["alpha"] = float(token.split(":")[1])
                    except (ValueError, IndexError):
                        raise ValueError(f"Invalid alpha value in strategy: {token}")
                else:
                    raise ValueError(f"Unknown strategy token: {token}")
            
            variant_name = "treatment" if len(body.treatment_strategies) == 1 else f"treatment_{i+1}"
            variants[variant_name] = params
            
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid strategy '{strategy_str}': {str(exc)}"
            )
    
    # Create experiment via ABTestingService
    try:
        await ab_service.create_experiment(
            experiment_id=body.experiment_id,
            description=body.description or "",
            variants=variants,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    
    return {"experiment_id": body.experiment_id}


@router.get("/shadow/{experiment_id}/comparison")
async def get_shadow_comparison(
    experiment_id: str,
    app_config: ConfigDep,
    ab_service: ABDep,
    date_from: date = Query(..., description="Start date for comparison"),
    date_to: date = Query(..., description="End date for comparison"),
) -> ShadowComparisonResponse:
    """Get shadow traffic comparison metrics between control and treatment (PR-08).
    
    Returns aggregated metrics for control vs treatment variants over the date range.
    Gated by ab_testing_enabled feature flag.
    
    Raises:
        HTTP 404: unknown experiment or no data for date range
    """
    _require_ab_enabled(app_config)
    
    try:
        # Get experiment to verify it exists
        experiment = await ab_service.get_experiment(experiment_id)
        if experiment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Experiment '{experiment_id}' not found"
            )
        
        # Get variant names (control + treatments)
        variant_names = list(experiment.variants.keys())
        if len(variant_names) < 2 or "control" not in variant_names:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Shadow experiment must have 'control' and at least one treatment variant"
            )
        
        # Compute metrics for each variant (aggregate over date range)
        from datetime import timedelta
        
        variant_metrics = {}
        
        for variant in variant_names:
            total_count: float = 0.0
            total_ctr: float = 0.0
            total_mrr: float = 0.0
            total_no_click_rate: float = 0.0
            total_latency_p95: float = 0.0
            days_with_data: int = 0
            
            # Aggregate metrics over date range
            current_date = date_from
            while current_date <= date_to:
                try:
                    daily_metrics = await ab_service.compute_daily_metrics(
                        experiment_id=experiment_id,
                        target_date=current_date,
                    )
                    
                    if variant in daily_metrics:
                        metrics = daily_metrics[variant]
                        if metrics.get("searches", 0) > 0:
                            total_count += metrics.get("searches", 0)
                            total_ctr += metrics.get("ctr", 0.0)
                            total_mrr += metrics.get("mrr", 0.0)
                            total_no_click_rate += metrics.get("no_click_rate", 0.0)
                            total_latency_p95 += metrics.get("latency_p95", 0.0)
                            days_with_data += 1
                    
                except ValueError:
                    # Experiment might not exist for this date
                    pass
                
                current_date += timedelta(days=1)
            
            # Calculate averages
            if days_with_data > 0:
                avg_ctr: float = total_ctr / days_with_data
                avg_mrr: float = total_mrr / days_with_data
                avg_no_click_rate: float = total_no_click_rate / days_with_data
                avg_latency_p95: float = total_latency_p95 / days_with_data
            else:
                avg_ctr = 0.0
                avg_mrr = 0.0
                avg_no_click_rate = 0.0
                avg_latency_p95 = 0.0
            
            variant_metrics[variant] = {
                "count": total_count,
                "ctr": avg_ctr,
                "mrr": avg_mrr,
                "no_click_rate": avg_no_click_rate,
                "latency_p95": avg_latency_p95,
            }
        
        # Return control vs treatment comparison
        # Find treatment variant (single treatment or first if multiple)
        treatment_variants = [v for v in variant_names if v != "control"]
        treatment_variant = treatment_variants[0] if treatment_variants else "treatment"
        
        control_metrics = variant_metrics["control"]
        treatment_metrics = variant_metrics[treatment_variant]
        
        return ShadowComparisonResponse(
            control=VariantMetrics(
                count=int(control_metrics["count"]),
                ctr=control_metrics["ctr"],
                mrr=control_metrics["mrr"],
                no_click_rate=control_metrics["no_click_rate"],
                latency_p95=control_metrics["latency_p95"]
            ),
            treatment=VariantMetrics(
                count=int(treatment_metrics["count"]),
                ctr=treatment_metrics["ctr"],
                mrr=treatment_metrics["mrr"],
                no_click_rate=treatment_metrics["no_click_rate"],
                latency_p95=treatment_metrics["latency_p95"]
            ),
        )
        
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
