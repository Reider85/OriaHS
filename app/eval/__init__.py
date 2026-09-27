"""Evaluation package initialization."""

from app.eval.datasets import EvalQuery, load_dataset, validate_dataset, get_query_stats
from app.eval.metrics import recall_at_10, ndcg_at_10, mrr
from app.eval.nightly import NightlyEvalJob, EvalReport

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