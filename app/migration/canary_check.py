"""Canary reindex check for migration validation (PR-07).

Provides canary similarity computation and validation logic.
"""

from typing import TYPE_CHECKING, Any

import structlog
from qdrant_client.http import models as qdrant_models
from sqlalchemy import func, select

from app.config import settings
from app.db.queries.model_migrations import update_canary_results
from app.db.session import Session
from app.embedding.service import EmbeddingService
from app.observability.metrics import migration_canary_similarity

if TYPE_CHECKING:
    pass

logger = structlog.get_logger("canary_check")


class CanaryReport:
    """Canary reindex results."""

    def __init__(
        self,
        migration_id: str,
        sample_count: int,
        avg_similarity: float | None,
        min_similarity: float | None,
        max_similarity: float | None,
        passed: bool,
        error: str | None = None,
    ):
        self.migration_id = migration_id
        self.sample_count = sample_count
        self.avg_similarity = avg_similarity
        self.min_similarity = min_similarity
        self.max_similarity = max_similarity
        self.passed = passed
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "migration_id": self.migration_id,
            "sample_count": self.sample_count,
            "avg_similarity": self.avg_similarity,
            "min_similarity": self.min_similarity,
            "max_similarity": self.max_similarity,
            "passed": self.passed,
            "error": self.error,
        }


def compute_canary_report(
    old_vectors: list[qdrant_models.PointStruct],
    new_embeddings: list[list[float]],
    min_threshold: float = 0.6,
    max_threshold: float = 0.95,
) -> CanaryReport:
    """Compute canary similarity report from old vectors and new embeddings.
    
    Args:
        old_vectors: List of Qdrant point structs with vectors
        new_embeddings: List of new embedding vectors
        min_threshold: Minimum similarity threshold (default: 0.6)
        max_threshold: Maximum average similarity threshold (default: 0.95)
    
    Returns:
        CanaryReport with similarity statistics and pass/fail status
    """
    if not old_vectors or not new_embeddings:
        return CanaryReport(
            migration_id="unknown",
            sample_count=0,
            avg_similarity=None,
            min_similarity=None,
            max_similarity=None,
            passed=False,
            error="No vectors or embeddings provided",
        )

    if len(old_vectors) != len(new_embeddings):
        return CanaryReport(
            migration_id="unknown",
            sample_count=0,
            avg_similarity=None,
            min_similarity=None,
            max_similarity=None,
            passed=False,
            error="Vector and embedding count mismatch",
        )

    similarities = []
    for i, old_point in enumerate(old_vectors):
        old_vector = old_point.vector
        new_vector = new_embeddings[i]

        # Compute cosine similarity
        sim = _cosine_similarity(old_vector, new_vector)
        similarities.append(sim)

    if not similarities:
        return CanaryReport(
            migration_id="unknown",
            sample_count=0,
            avg_similarity=None,
            min_similarity=None,
            max_similarity=None,
            passed=False,
            error="No valid similarity computations",
        )

    avg_sim = sum(similarities) / len(similarities)
    min_sim = min(similarities)
    max_sim = max(similarities)

    # Check thresholds
    passed = min_sim >= min_threshold and avg_sim <= max_threshold

    return CanaryReport(
        migration_id="unknown",
        sample_count=len(similarities),
        avg_similarity=avg_sim,
        min_similarity=min_sim,
        max_similarity=max_sim,
        passed=passed,
    )


async def run_canary_check(
    session: Session,
    migration_id: str,
    old_embedding_service: EmbeddingService,
    new_embedding_service: EmbeddingService,
    percentage: float = 0.01,
    min_threshold: float = 0.6,
    max_threshold: float = 0.95,
) -> CanaryReport:
    """Run canary check by sampling documents and comparing embeddings.
    
    Args:
        session: Database session
        migration_id: Migration ID for reporting
        old_embedding_service: Service for old model embeddings
        new_embedding_service: Service for new model embeddings
        percentage: Percentage of documents to sample
        min_threshold: Minimum similarity threshold
        max_threshold: Maximum average similarity threshold
    
    Returns:
        CanaryReport with results
    """
    # Sample documents
    total_docs = session.scalar(
        select(func.count())
        .select_from(EmbeddingModel.__table__)
    )

    target_count = max(1, int(total_docs * percentage))
    max_docs = settings.migration.max_canary_docs

    if target_count > max_docs:
        target_count = max_docs

    # Get document IDs for canary using deterministic sampling
    doc_ids = session.scalars(
        select(EmbeddingModel.id)
        .where(func.hashtext(EmbeddingModel.name) < percentage * 100)
        .order_by(func.md5(EmbeddingModel.name))
        .limit(target_count)
    ).all()

    if not doc_ids:
        return CanaryReport(
            migration_id=migration_id,
            sample_count=0,
            avg_similarity=None,
            min_similarity=None,
            max_similarity=None,
            passed=False,
            error="No documents found for canary sample",
        )

    # Retrieve old vectors from Qdrant
    old_vectors = await old_embedding_service.qdrant_client.retrieve(
        old_embedding_service.config.collection_name,
        list(doc_ids),
        with_vector=True,
    )

    # Get document contents for new embeddings
    documents = session.scalars(
        select(EmbeddingModel)
        .where(EmbeddingModel.id.in_(doc_ids))
    ).all()

    # Create content-to-id mapping
    content_to_id = {doc.content: doc.id for doc in documents}

    # Generate new embeddings
    contents = list(content_to_id.keys())
    new_embeddings = await new_embedding_service.embed_texts(contents)

    # Compute similarities
    similarities = []
    for old_point in old_vectors:
        doc_id = old_point.id
        old_vector = old_point.vector

        # Find corresponding new embedding
        new_vector = None
        for i, content in enumerate(contents):
            if content_to_id[content] == doc_id:
                new_vector = new_embeddings[i]
                break

        if new_vector is None:
            continue

        # Compute cosine similarity
        sim = _cosine_similarity(old_vector, new_vector)
        similarities.append(sim)

    if not similarities:
        return CanaryReport(
            migration_id=migration_id,
            sample_count=0,
            avg_similarity=None,
            min_similarity=None,
            max_similarity=None,
            passed=False,
            error="No valid similarity computations",
        )

    avg_sim = sum(similarities) / len(similarities)
    min_sim = min(similarities)
    max_sim = max(similarities)

    # Check thresholds
    passed = min_sim >= min_threshold and avg_sim <= max_threshold

    # Update migration record
    update_canary_results(
        session,
        migration_id,
        avg_sim,
        min_sim,
        max_sim,
        passed,
    )

    # Update metrics
    migration_canary_similarity.set(
        avg_sim,
        labels={"migration_id": migration_id, "kind": "avg"}
    )
    migration_canary_similarity.set(
        min_sim,
        labels={"migration_id": migration_id, "kind": "min"}
    )
    migration_canary_similarity.set(
        max_sim,
        labels={"migration_id": migration_id, "kind": "max"}
    )

    logger.info(
        "Canary check completed",
        migration_id=migration_id,
        sample_count=len(similarities),
        avg_similarity=avg_sim,
        min_similarity=min_sim,
        max_similarity=max_sim,
        passed=passed,
        min_threshold=min_threshold,
        max_threshold=max_threshold,
    )

    return CanaryReport(
        migration_id=migration_id,
        sample_count=len(similarities),
        avg_similarity=avg_sim,
        min_similarity=min_sim,
        max_similarity=max_sim,
        passed=passed,
    )


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def sample_document_ids(
    session: Session,
    total: int,
    percentage: float,
    max_docs: int | None = None,
) -> list[str]:
    """Sample document IDs deterministically.
    
    Uses hash-based sampling for reproducible results.
    """
    target_count = max(1, int(total * percentage))

    if max_docs:
        target_count = min(target_count, max_docs)

    # Sample using hash-based deterministic selection
    doc_ids = session.scalars(
        select(EmbeddingModel.id)
        .where(func.hashtext(EmbeddingModel.name) < percentage * 100)
        .order_by(func.md5(EmbeddingModel.name))
        .limit(target_count)
    ).all()

    return list(doc_ids)


__all__ = ["CanaryReport", "compute_canary_report", "run_canary_check", "sample_document_ids"]
