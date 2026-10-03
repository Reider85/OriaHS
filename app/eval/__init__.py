"""Evaluation package initialization."""

from app.eval.datasets import EvalQuery, get_query_stats, load_dataset, validate_dataset
from app.eval.metrics import mrr, ndcg_at_10, recall_at_10
from app.eval.nightly import EvalReport, NightlyEvalJob

__all__ = [
    "EvalQuery",
    "load_dataset",
    "validate_dataset",
    "get_query_stats",
    "recall_at_10",
    "ndcg_at_10",
    "mrr",
    "NightlyEvalJob",
    "EvalReport",
]
