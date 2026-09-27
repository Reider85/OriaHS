"""Evaluation dataset loading (C-07)."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EvalQuery(BaseModel):
    """A single evaluation query with relevance judgments.
    
    Matches the JSONL format in eval/datasets/baseline_v1.jsonl.
    """
    query_id: str = Field(..., description="Unique identifier for the query")
    query: str = Field(..., description="The search query text")
    tenant_id: str = Field(..., description="Tenant ID for the query")
    relevant_doc_ids: list[str] = Field(..., description="List of relevant document IDs")
    language: str = Field(..., description="Query language (e.g., 'en', 'ru')")


def load_dataset(path: str) -> list[EvalQuery]:
    """Load evaluation dataset from JSONL file.
    
    Args:
        path: Path to JSONL file where each line is a JSON object
        
    Returns:
        List of EvalQuery objects
        
    Raises:
        FileNotFoundError: If the path doesn't exist
        json.JSONDecodeError: If any line is invalid JSON
        ValueError: If required fields are missing
    """
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Eval dataset not found: {path}")
    
    queries: list[EvalQuery] = []
    
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue  # Skip empty lines
                
            try:
                data = json.loads(line)
                query = EvalQuery(**data)
                queries.append(query)
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"Invalid JSON on line {line_num} in {path}: {e}")
    
    return queries


def validate_dataset(queries: list[EvalQuery]) -> None:
    """Validate that all queries have required data.
    
    Args:
        queries: List of EvalQuery objects to validate
        
    Raises:
        ValueError: If any query is invalid
    """
    for i, query in enumerate(queries, start=1):
        if not query.query_id:
            raise ValueError(f"Query {i}: missing query_id")
        if not query.query.strip():
            raise ValueError(f"Query {i}: empty query text")
        if not query.tenant_id:
            raise ValueError(f"Query {i}: missing tenant_id")
        if not query.relevant_doc_ids:
            raise ValueError(f"Query {i}: empty relevant_doc_ids")
        # Convert doc_ids to strings for consistency
        if not all(isinstance(doc_id, str) for doc_id in query.relevant_doc_ids):
            raise ValueError(f"Query {i}: relevant_doc_ids must be strings")


def get_query_stats(queries: list[EvalQuery]) -> dict[str, Any]:
    """Get statistics about the dataset.
    
    Args:
        queries: List of EvalQuery objects
        
    Returns:
        Dictionary with dataset statistics
    """
    total_queries = len(queries)
    total_relevant = sum(len(q.relevant_doc_ids) for q in queries)
    avg_relevant_per_query = total_relevant / total_queries if total_queries > 0 else 0
    languages = {q.language for q in queries}
    
    return {
        "total_queries": total_queries,
        "total_relevant_documents": total_relevant,
        "avg_relevant_per_query": avg_relevant_per_query,
        "languages": sorted(languages),
        "unique_tenant_ids": sorted({q.tenant_id for q in queries}),
    }


__all__ = ["EvalQuery", "load_dataset", "validate_dataset", "get_query_stats"]