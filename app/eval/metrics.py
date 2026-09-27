"""Evaluation metrics calculation (C-07).

Pure functions for computing Recall@10, nDCG@10, and MRR from search results
vs. ground truth relevance judgments.
"""

import math
from typing import Set


def recall_at_10(retrieved: list[str], relevant: Set[str]) -> float:
    """Compute Recall@10: |retrieved ∩ relevant| / |relevant|.
    
    Args:
        retrieved: List of document IDs in ranked order (top-10)
        relevant: Set of truly relevant document IDs
        
    Returns:
        Recall@10 score between 0.0 and 1.0
    """
    if not relevant:
        return 0.0  # Avoid division by zero
    
    retrieved_set = set(retrieved[:10])  # Only consider top-10
    intersection = retrieved_set & relevant
    return len(intersection) / len(relevant)


def mrr(retrieved: list[str], relevant: Set[str]) -> float:
    """Compute Mean Reciprocal Rank: 1 / rank of first relevant document.
    
    Args:
        retrieved: List of document IDs in ranked order
        relevant: Set of truly relevant document IDs
        
    Returns:
        MRR score between 0.0 and 1.0, or 0.0 if no relevant docs in top-10
    """
    for rank, doc_id in enumerate(retrieved[:10], start=1):  # Only top-10
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_10(retrieved: list[str], relevant: Set[str]) -> float:
    """Compute nDCG@10 with binary relevance.
    
    Uses DCG = Σ rel_i / log2(i+1), where rel_i = 1 if doc_i is relevant, else 0.
    nDCG = DCG / IDCG (ideal DCG with all relevant docs in perfect order).
    
    Args:
        retrieved: List of document IDs in ranked order (top-10)
        relevant: Set of truly relevant document IDs
        
    Returns:
        nDCG@10 score between 0.0 and 1.0
    """
    if not relevant:
        return 0.0
    
    # Compute DCG
    dcg = 0.0
    for rank, doc_id in enumerate(retrieved[:10], start=1):
        relevance = 1.0 if doc_id in relevant else 0.0
        if relevance > 0:
            dcg += relevance / math.log2(rank + 1)
    
    # Compute IDCG (perfect ranking: all relevant docs first)
    idcg = 0.0
    for rank in range(min(len(relevant), 10)):
        idcg += 1.0 / math.log2(rank + 1)
    
    if idcg == 0:
        return 0.0
    
    return dcg / idcg


__all__ = ["recall_at_10", "ndcg_at_10", "mrr"]