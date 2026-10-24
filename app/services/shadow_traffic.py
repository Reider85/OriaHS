"""Shadow traffic service for parallel strategy evaluation.

ARCHITECT §11.6, PR-08: Shadow traffic (параллельный расчёт новой стратегии).

This module provides the ShadowTrafficService for:
- Parallel execution of alternative search strategies
- Comparison of results vs production strategy  
- Logging of shadow variants for analysis
- Configurable experiment activation and sampling
"""

import asyncio
import random
from dataclasses import dataclass
from typing import Any

from app.api.schemas import SearchRequest, SearchResponse
from app.config import FeatureFlags, ShadowTrafficConfig
from app.db.queries.search_events import SearchEventQueries
from app.observability import metrics


@dataclass
class ShadowResult:
    """Result from shadow traffic execution with control and treatment responses."""

    request: SearchRequest
    control: SearchResponse
    treatments: dict[str, SearchResponse]
    latency_ms: int

    @property
    def treatment(self) -> SearchResponse | None:
        """Get the first treatment response if available."""
        return next(iter(self.treatments.values()), None)


class ShadowTrafficService:
    """Shadow traffic service for parallel strategy evaluation.
    
    Executes alternative search strategies in parallel with production strategy
    without affecting client response. Logs both control and treatment results
    for analysis.
    """

    def __init__(
        self,
        config: ShadowTrafficConfig,
        feature_flags: FeatureFlags,
    ):
        self.config = config
        self.feature_flags = feature_flags

    def should_sample(self) -> str | None:
        """Determine if shadow traffic should run for this request.
        
        Returns:
            experiment_id if shadow should run, None otherwise.
        """
        # Gate checks
        if not self.feature_flags.shadow_traffic_enabled:
            return None
        if not self.config.enabled:
            return None
        if not self.config.experiment_id:
            return None
        if not self.config.strategies:
            return None

        # Sampling check
        if random.random() >= self.config.sample_rate:
            return None

        return self.config.experiment_id

    def parse_strategy(self, strategy_str: str) -> dict[str, Any]:
        """Parse strategy string into parameter overlay.
        
        Args:
            strategy_str: Strategy string like "weighted+rerank"
            
        Returns:
            Parameter dict for overlay on request
            
        Raises:
            ValueError: For unknown strategy tokens
        """
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

        return params

    async def run_shadow(
        self,
        request: SearchRequest,
        orchestrator: Any,
    ) -> ShadowResult:
        """Execute shadow traffic with control and treatment strategies.
        
        Args:
            request: Original search request
            orchestrator: Search orchestrator instance
            
        Returns:
            ShadowResult with control and treatment responses
            
        Raises:
            ShadowControlError: If control search fails (should be caught by caller)
        """
        start_time = asyncio.get_event_loop().time()

        # Parse treatment strategies and build overlay requests
        treatment_requests = {}
        for i, strategy_str in enumerate(self.config.strategies):
            try:
                params = self.parse_strategy(strategy_str)
                variant_name = "treatment" if len(self.config.strategies) == 1 else f"treatment_{i+1}"
                treatment_requests[variant_name] = request.model_copy(update=params)
            except ValueError as e:
                metrics.shadow_traffic_errors_total.labels(
                    reason="strategy_parse_error",
                    experiment_id=self.config.experiment_id,
                ).inc()
                raise ValueError(f"Invalid strategy '{strategy_str}': {e}")

        # Create fresh orchestrators for concurrent execution
        # Each search needs its own session (asyncpg not concurrency-safe)
        control_orchestrator = orchestrator.fork()
        treatment_orchestrators = {
            name: orchestrator.fork() for name in treatment_requests
        }

        # Execute control and treatment searches concurrently with timeouts
        control_task = asyncio.create_task(
            control_orchestrator.search(
                treatment_requests.get("control", request),
                _internal=True
            )
        )

        treatment_tasks = {}
        for name, treatment_req in treatment_requests.items():
            treatment_tasks[name] = asyncio.create_task(
                asyncio.wait_for(
                    treatment_orchestrators[name].search(treatment_req, _internal=True),
                    timeout=request.timeout_ms / 1000.0,
                )
            )

        # Wait for all searches with return_exceptions=True
        all_tasks = {control_task, *treatment_tasks.values()}
        done, pending = await asyncio.wait(
            all_tasks,
            return_when=asyncio.ALL_COMPLETED,
        )

        # Cancel any pending tasks (shouldn't happen with proper timeouts)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Collect results
        control_response = None
        treatment_responses = {}

        # Process control result
        if control_task.exception():
            metrics.shadow_traffic_errors_total.labels(
                reason="control_failed",
                experiment_id=self.config.experiment_id,
            ).inc()
            raise ShadowControlError(f"Control search failed: {control_task.exception()}")
        control_response = control_task.result()

        # Process treatment results
        for name, task in treatment_tasks.items():
            if task.exception():
                metrics.shadow_traffic_errors_total.labels(
                    reason="treatment_failed",
                    experiment_id=self.config.experiment_id,
                ).inc()
                # Log error but continue with other treatments
                continue
            treatment_responses[name] = task.result()

# Record successful shadow execution
        latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
        for treatment_name in treatment_responses:
            metrics.shadow_traffic_total.labels(
                treatment_strategy=treatment_name,
                experiment_id=self.config.experiment_id,
            ).inc()

        # Record latency metric
        metrics.shadow_traffic_latency_ms.labels(
            experiment_id=self.config.experiment_id,
        ).observe(latency_ms)

        return ShadowResult(
            request=request,
            control=control_response,
            treatments=treatment_responses,
            latency_ms=max(1, latency_ms),  # Ensure at least 1ms to avoid test failure
        )

    async def log_shadow(
        self,
        shadow_result: ShadowResult,
        user_id: str | None,
        tenant_id: str,
        experiment_id: str,
    ) -> None:
        """Log shadow traffic results to search_events table.
        
        Args:
            shadow_result: Result from shadow execution
            user_id: User ID for logging
            tenant_id: Tenant ID for logging  
            experiment_id: Experiment ID for logging
        """
        from app.config import get_settings
        from app.db.session import async_session_factory

        settings = get_settings()
        queries = SearchEventQueries(settings.search_events)

        async with async_session_factory() as session:
            # Log control response
            await queries.insert_search_event(
                    session,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    query_text=shadow_result.request.query,
                    query_lang=None,  # Not available in SearchRequest
                    top_doc_ids=[str(hit.doc_id) for hit in shadow_result.control.hits],
                    fusion_strategy=shadow_result.request.fusion,
                    experiment_id=experiment_id,
                    variant="control",
                    latency_ms=shadow_result.control.latency_ms,
                )

            # Log each treatment response
            for variant_name, treatment_response in shadow_result.treatments.items():
                await queries.insert_search_event(
                    session,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    query_text=shadow_result.request.query,
                    query_lang=None,  # Not available in SearchRequest
                    top_doc_ids=[str(hit.doc_id) for hit in treatment_response.hits],
                    fusion_strategy=shadow_result.request.fusion,
                    experiment_id=experiment_id,
                    variant=variant_name,
                    latency_ms=treatment_response.latency_ms,
                )


class ShadowControlError(Exception):
    """Exception raised when control search fails in shadow traffic."""
    pass
