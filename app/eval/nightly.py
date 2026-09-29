"""Nightly evaluation job (C-07).

Runs offline evaluation of all fusion strategies on eval dataset,
computes metrics, saves results, detects regressions, and blocks release.
"""

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchRequest
from app.config import AppConfig, EvalConfig, SearchConfig
from app.db.queries import eval as eval_queries
from app.db.queries.outbox import count_pending
from app.db.session import async_session_factory
from app.eval.datasets import EvalQuery, load_dataset, validate_dataset
from app.eval.metrics import recall_at_10, ndcg_at_10, mrr
from app.observability import metrics
from app.search.orchestrator import SearchOrchestrator
from app.search.qdrant_payload import QdrantPayload
from app.services.qdrant import QdrantService

logger = logging.getLogger(__name__)


@dataclass
class EvalReport:
    """Report of nightly evaluation results."""
    
    dataset_name: str
    dataset_version: str
    strategies: dict[str, dict[str, float]]  # strategy -> {metric: value}
    baseline_metrics: dict[str, dict[str, float]]  # strategy -> {metric: value}
    regressions: dict[str, dict[str, float]]  # strategy -> {metric: regression_amount}
    regression_detected: bool
    run_duration_seconds: float
    git_sha: str
    queries_processed: int


class NightlyEvalJob:
    """Offline evaluation job (C-07).
    
    Loads eval dataset, registers it, runs all fusion strategies,
    computes metrics, saves results, and detects regressions.
    """
    
    def __init__(
        self,
        session_factory: AsyncSession | None = None,
        search_config: SearchConfig | None = None,
        eval_config: EvalConfig | None = None,
        app_config: AppConfig | None = None,
    ) -> None:
        self._session_factory = session_factory or async_session_factory
        self._search_config = search_config or app_config.search if app_config else SearchConfig()
        self._eval_config = eval_config or app_config.eval if app_config else EvalConfig()
        self._app_config = app_config or AppConfig()
        
        # Initialize dependencies
        self._qdrant_service = QdrantService()
        self._orchestrator = None  # Lazy init when needed
        
    def _get_orchestrator(self) -> SearchOrchestrator:
        """Get or create SearchOrchestrator with required dependencies."""
        if self._orchestrator is None:
            # Create minimal orchestrator for eval (no reranker needed for RRF/weighted)
            self._orchestrator = SearchOrchestrator(
                session=self._session_factory(),  # Will be overridden per-query
                qdrant_client=self._qdrant_service.get_async_client(),
                embedding_service=None,  # Not needed for eval queries
                embedding_cache=None,    # Not needed for eval queries
                config=self._search_config,
                reranker=None,           # Eval runs RRF/weighted only
                circuit_breaker=None,    # Not needed for eval
                speculative_reranker=None, # Not needed for eval
                reranker_config=None,    # Not needed for eval
                feature_flags=self._app_config.feature_flags,
            )
        return self._orchestrator
    
    async def run(self) -> EvalReport:
        """Execute the full nightly evaluation pipeline.
        
        Returns:
            EvalReport with results and regression status
        """
        start_time = time.monotonic()
        
        try:
            logger.info("Nightly eval started")
            
            # Load and validate dataset
            queries = load_dataset(self._eval_config.dataset_path)
            validate_dataset(queries)
            
            # Register dataset in DB
            dataset = await eval_queries.register_dataset(
                session=await self._session_factory(),
                name="baseline_v1",
                version="v1",
                path=self._eval_config.dataset_path,
                query_count=len(queries),
            )
            
            # Get current git SHA for reproducibility
            git_sha = self._get_git_sha()
            
            # Run evaluation for each strategy
            strategies = ["rrf", "weighted", "weighted+rerank"]
            strategy_results = {}
            baseline_metrics = {}
            regressions = {}
            
            for strategy in strategies:
                logger.info(f"Running evaluation for strategy: {strategy}")
                
                # Run eval for this strategy
                results = await self._run_strategy_evaluation(queries, strategy)
                strategy_results[strategy] = results
                
                # Get baseline for comparison
                baseline = await eval_queries.get_baseline(
                    await self._session_factory(), 
                    str(dataset.id), 
                    strategy
                )
                if baseline:
                    baseline_metrics[strategy] = {
                        "recall_at_10": baseline.recall_at_10,
                        "ndcg_at_10": baseline.ndcg_at_10,
                        "mrr": baseline.mrr,
                    }
                
                # Check for regressions
                regression = self._check_regression(
                    strategy, results, baseline_metrics.get(strategy, {})
                )
                if regression:
                    regressions[strategy] = regression
                    logger.warning(
                        f"Regression detected in {strategy}: {regression}",
                        extra={"strategy": strategy, "results": results, "baseline": baseline_metrics.get(strategy)}
                    )
            
            # Save results to database
            for strategy, results in strategy_results.items():
                await eval_queries.save_result(
                    session=await self._session_factory(),
                    dataset_id=str(dataset.id),
                    strategy=strategy,
                    recall_at_10=results["recall_at_10"],
                    ndcg_at_10=results["ndcg_at_10"],
                    mrr=results["mrr"],
                    git_sha=git_sha,
                    extra={"queries_processed": len(queries)},
                )
            
            # Publish metrics
            self._publish_metrics(strategy_results, git_sha)
            
            # Log worst performing queries
            self._log_worst_queries(queries, strategy_results)
            
            run_duration = time.monotonic() - start_time
            regression_detected = bool(regressions)
            
            report = EvalReport(
                dataset_name=dataset.name,
                dataset_version=dataset.version,
                strategies=strategy_results,
                baseline_metrics=baseline_metrics,
                regressions=regressions,
                regression_detected=regression_detected,
                run_duration_seconds=run_duration,
                git_sha=git_sha,
                queries_processed=len(queries),
            )
            
            logger.info("Nightly eval completed", extra={"report": report})
            
            return report
            
        except Exception as e:
            logger.exception("Nightly eval failed", extra={"error": str(e)})
            raise
    
    async def _run_strategy_evaluation(
        self, queries: list[EvalQuery], strategy: str
    ) -> dict[str, float]:
        """Run evaluation for a single strategy.
        
        Args:
            queries: List of evaluation queries
            strategy: Strategy name ('rrf', 'weighted', 'weighted+rerank')
            
        Returns:
            Dictionary with recall_at_10, ndcg_at_10, mrr
        """
        recall_scores = []
        ndcg_scores = []
        mrr_scores = []
        
        # Build search request based on strategy
        if strategy == "rrf":
            search_request = SearchRequest(
                query="",  # Will be set per query
                tenant_id=queries[0].tenant_id,
                top_k=10,  # Standard eval top-K
                fusion="rrf",
                rerank=False,
                timeout_ms=1000,  # Generous timeout for eval
            )
        elif strategy == "weighted":
            search_request = SearchRequest(
                query="",  # Will be set per query
                tenant_id=queries[0].tenant_id,
                top_k=10,
                fusion="weighted",
                rerank=False,
                fusion_alpha=0.5,
                timeout_ms=1000,
            )
        elif strategy == "weighted+rerank":
            search_request = SearchRequest(
                query="",  # Will be set per query
                tenant_id=queries[0].tenant_id,
                top_k=10,
                fusion="weighted",
                rerank=True,
                fusion_alpha=0.5,
                timeout_ms=1000,
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
        
        # Get orchestrator instance
        orchestrator = self._get_orchestrator()
        
        # Process each query
        for query in queries:
            search_request.query = query.query
            
            try:
                # Run search (internal call, no HTTP overhead)
                response = await orchestrator.search(search_request)
                
                # Extract top-10 doc_ids
                retrieved_doc_ids = [hit.doc_id for hit in response.hits[:10]]
                
                # Convert to strings for comparison (метрики оперируют str)
                retrieved_ids = [str(doc_id) for doc_id in retrieved_doc_ids]
                relevant_ids = set(str(doc_id) for doc_id in query.relevant_doc_ids)
                
                # Compute metrics
                recall_scores.append(recall_at_10(retrieved_ids, relevant_ids))
                ndcg_scores.append(ndcg_at_10(retrieved_ids, relevant_ids))
                mrr_scores.append(mrr(retrieved_ids, relevant_ids))
                
            except Exception as e:
                logger.warning(
                    "Query evaluation failed, skipping",
                    extra={"query_id": query.query_id, "error": str(e)}
                )
                # Give worst possible score for failed queries
                recall_scores.append(0.0)
                ndcg_scores.append(0.0)
                mrr_scores.append(0.0)
        
        return {
            "recall_at_10": np.mean(recall_scores),
            "ndcg_at_10": np.mean(ndcg_scores),
            "mrr": np.mean(mrr_scores),
        }
    
    def _check_regression(
        self, 
        strategy: str, 
        current: dict[str, float], 
        baseline: dict[str, float] | None
    ) -> dict[str, float] | None:
        """Check if current metrics show regression against baseline.
        
        Returns:
            Dictionary with regression amounts, or None if no regression
        """
        if not baseline:
            return None  # No baseline to compare against
        
        regressions = {}
        threshold = self._eval_config.recall_regression_threshold
        
        if "recall_at_10" in baseline:
            regression = baseline["recall_at_10"] - current["recall_at_10"]
            if regression > threshold:
                regressions["recall_at_10"] = regression
        
        threshold = self._eval_config.ndcg_regression_threshold
        if "ndcg_at_10" in baseline:
            regression = baseline["ndcg_at_10"] - current["ndcg_at_10"]
            if regression > threshold:
                regressions["ndcg_at_10"] = regression
        
        return regressions if regressions else None
    
    def _publish_metrics(self, strategy_results: dict[str, dict[str, float]], git_sha: str) -> None:
        """Publish evaluation metrics to Prometheus."""
        for strategy, metrics_dict in strategy_results.items():
            metrics.eval_recall_at_10.labels(
                strategy=strategy, 
                dataset_version="v1"
            ).set(metrics_dict["recall_at_10"])
            
            metrics.eval_ndcg_at_10.labels(
                strategy=strategy, 
                dataset_version="v1"
            ).set(metrics_dict["ndcg_at_10"])
            
            metrics.eval_mrr.labels(
                strategy=strategy, 
                dataset_version="v1"
            ).set(metrics_dict["mrr"])
            
            metrics.eval_runs_total.labels(strategy=strategy).inc()
    
    def _log_worst_queries(self, queries: list[EvalQuery], strategy_results: dict[str, dict[str, float]]) -> None:
        """Log the 3 worst performing queries for debugging."""
        # This is a simplified version - in production you might want to track per-query performance
        worst_queries = []
        for query in queries[:3]:  # Just log first 3 for now
            worst_queries.append({
                "query_id": query.query_id,
                "query": query.query[:100] + "..." if len(query.query) > 100 else query.query,
                "relevant_count": len(query.relevant_doc_ids),
            })
        
        logger.info("Sample queries for debugging", extra={"worst_queries": worst_queries})
    
    def _get_git_sha(self) -> str:
        """Get current git SHA for reproducibility."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=Path(__file__).parent.parent.parent
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Could not get git SHA, using unknown")
            return "unknown"


async def _main() -> None:
    """Main entry point for the nightly eval job."""
    from app.observability.logging import setup_logging
    
    setup_logging(level="INFO")
    
    job = NightlyEvalJob()
    report = await job.run()
    
    if report.regression_detected:
        logger.error("Regression detected - blocking release")
        exit(1)
    else:
        logger.info("No regressions detected - release safe")
        exit(0)


if __name__ == "__main__":
    asyncio.run(_main())