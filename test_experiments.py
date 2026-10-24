@router.get("/shadow/{experiment_id}/comparison")
async def get_shadow_comparison(
    experiment_id: str,
    date_from: date,
    date_to: date,
    app_config: ConfigDep,
    ab_service: ABDep,
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
        
        # Compute metrics for each variant
        variant_metrics = {}
        
        for variant in variant_names:
            metrics = await ab_service.compute_daily_metrics(
                experiment_id=experiment_id,
                variant=variant,
                date_from=date_from,
                date_to=date_to,
            )
            
            if metrics is None:
                # No data for this variant in date range
                variant_metrics[variant] = VariantMetrics(
                    count=0,
                    ctr=0.0,
                    mrr=0.0,
                    no_click_rate=0.0,
                    latency_p95=0.0,
                )
            else:
                variant_metrics[variant] = VariantMetrics(
                    count=metrics.count,
                    ctr=metrics.ctr,
                    mrr=metrics.mrr,
                    no_click_rate=metrics.no_click_rate,
                    latency_p95=metrics.latency_p95,
                )
        
        # Return control vs treatment comparison
        # Find treatment variant (single treatment or first if multiple)
        treatment_variants = [v for v in variant_names if v != "control"]
        treatment_variant = treatment_variants[0] if treatment_variants else "treatment"
        
        return ShadowComparisonResponse(
            control=variant_metrics["control"],
            treatment=variant_metrics[treatment_variant],
        )
        
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))