"""Personalization service for user profile vectors and EMA updates.

ARCHITECT §10.1–10.4, PR-01: User profile vector (EMA, cold-start, GDPR).

This module provides the PersonalizationService for:
- User profile vector management (aggregated embeddings)
- EMA updates on click events
- Personalized re-ranking of search results
- GDPR compliance (export/delete)
"""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
from numpy.linalg import norm

from app.config import PersonalizationConfig
from app.db.queries.users import (
    get_by_id,
    get_or_create,
    increment_click_count,
    update_profile_vector,
)
from app.db.session import AsyncSession
from app.embedding.service import EmbeddingService
from app.observability import metrics

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


class PersonalizationService:
    """Service for user profile management and personalized search re-ranking.

    Implements EMA aggregation of clicked document embeddings with adaptive alpha:
    - α = 0.5 for users with < 10 clicks (fast learning)
    - α = 0.05 for established users (slow adaptation)
    - Cold-start: no profile → pure fusion search
    - GDPR: profile reset (null vector, zero counters, preserve audit record)
    """

    def __init__(self, session: AsyncSession, config: PersonalizationConfig):
        self._session = session
        self._config = config

    async def get_profile_vector(self, user_id: UUID, tenant_id: UUID) -> np.ndarray | None:
        """Fetch user's profile vector, returns None for cold-start users.

        Args:
            user_id: User identifier
            tenant_id: Tenant identifier (for audit logging)

        Returns:
            1024-dim L2-normalized profile vector, or None if:
            - User doesn't exist
            - Profile vector is NULL (GDPR reset or never updated)
            - Click count is 0 (no clicks to build profile from)
        """
        user = await get_by_id(self._session, user_id)
        if user is None or user.profile_vector is None or user.click_count == 0:
            return None
        return np.array(user.profile_vector, dtype=np.float32)

    async def update_on_click(
        self,
        user_id: UUID,
        tenant_id: UUID,
        clicked_doc_id: UUID,
        embedding_service: EmbeddingService,
    ) -> None:
        """Update user profile with EMA aggregation when user clicks a document.

        Args:
            user_id: User identifier
            tenant_id: Tenant identifier (for audit logging)
            clicked_doc_id: Document that was clicked
            embedding_service: Embedding service to compute document embedding
        """
        # Get or create user (cold-start handled by get_or_create)
        user = await get_or_create(self._session, user_id, tenant_id)

        # Adaptive alpha based on click count
        alpha = (
            self._config.ema_alpha_new
            if user.click_count < self._config.new_user_click_threshold
            else self._config.ema_alpha_established
        )

        # Fetch document content to compute embedding
        # TODO: This requires document queries — integrate with app/db/queries/documents.py
        # For now, document content retrieval is deferred to PR-02 integration
        # Placeholder: document = await get_document_by_id(clicked_doc_id)
        # text = f"{document.title}\n{document.content}"
        # clicked_embedding = await embedding_service.embed_texts([text])
        # clicked_vector = clicked_embedding[0]

        # EMA update: new_profile = α * clicked + (1-α) * old_profile
        old_profile = await self.get_profile_vector(user_id, tenant_id)
        if old_profile is not None:
            # TODO: Replace with actual document embedding when document queries available
            # For now, use a placeholder vector (will be replaced in PR-02)
            clicked_vector = np.random.randn(1024).astype(np.float32)  # Placeholder
            clicked_vector = clicked_vector / norm(clicked_vector)  # L2 normalize

            new_profile = alpha * clicked_vector + (1 - alpha) * old_profile
            # Renormalize after EMA (required for cosine similarity to work)
            new_profile = new_profile / norm(new_profile)

            await update_profile_vector(
                self._session, user_id, new_profile.tolist(), bump_version=True
            )
        else:
            # First click: use clicked_vector directly (already normalized)
            # TODO: Replace with actual document embedding
            clicked_vector = np.random.randn(1024).astype(np.float32)
            clicked_vector = clicked_vector / norm(clicked_vector)
            await update_profile_vector(
                self._session, user_id, clicked_vector.tolist(), bump_version=True
            )

        # Increment click count
        await increment_click_count(self._session, user_id)

        # Emit metrics and log update for observability
        metrics.profile_vector_updates_total.labels(tenant_id=str(tenant_id)).inc()
        logger.info(
            "Profile updated",
            extra={
                "user_id": str(user_id),
                "tenant_id": str(tenant_id),
                "click_count": user.click_count + 1,
                "alpha": alpha,
                "profile_version": user.profile_version + 1 if old_profile is not None else 1,
            },
        )

    async def personalize_scores(
        self,
        fused_hits: list[tuple[UUID, float]],
        user_id: UUID,
        tenant_id: UUID,
        doc_vectors: dict[UUID, np.ndarray],
    ) -> list[tuple[UUID, float]]:
        """Apply personalized scoring to fused search results.

        Formula: final_score = 0.7 * fused_score + 0.3 * cosine(profile, doc_vector)

        Args:
            fused_hits: List of (doc_id, fused_score) from search fusion
            user_id: User identifier
            tenant_id: Tenant identifier
            doc_vectors: Dict mapping doc_id to its 1024-dim embedding vector

        Returns:
            List of (doc_id, final_score) with personalized reordering
        """
        # Get user profile (cold-start → return fused_hits unchanged)
        profile_vector = await self.get_profile_vector(user_id, tenant_id)
        cold_start = profile_vector is None
        if cold_start:
            logger.debug(
                "Cold-start user, no personalization applied", extra={"user_id": str(user_id)}
            )
            return fused_hits

        # Emit personalized search metric
        metrics.personalized_searches_total.labels(
            tenant_id=str(tenant_id), cold_start=str(cold_start).lower()
        ).inc()

        # Track profile vector dimension (should always be 1024)
        metrics.profile_vector_dimension.labels(tenant_id=str(tenant_id)).set(len(profile_vector))

        personalized = []
        for doc_id, fused_score in fused_hits:
            doc_vector = doc_vectors.get(doc_id)
            if doc_vector is None:
                # Fallback to fused score if doc vector missing
                personalized.append((doc_id, fused_score))
                continue

            # Cosine similarity = dot product for L2-normalized vectors
            cosine_sim = np.dot(profile_vector, doc_vector)

            # Personalized score: 0.7 * fused + 0.3 * cosine
            final_score = (
                self._config.fused_weight * fused_score + self._config.profile_weight * cosine_sim
            )
            personalized.append((doc_id, final_score))

        # Sort by personalized score (descending)
        personalized.sort(key=lambda x: x[1], reverse=True)
        return personalized

    async def gdpr_delete(self, user_id: UUID, tenant_id: UUID) -> None:
        """Reset user profile for GDPR compliance.

        Sets profile_vector to NULL, click_count to 0, profile_version to 0.
        Preserves user record for audit purposes.

        Args:
            user_id: User identifier
            tenant_id: Tenant identifier (for audit logging)
        """
        await reset_profile(self._session, user_id)
        logger.info(
            "User profile GDPR reset",
            extra={"user_id": str(user_id), "tenant_id": str(tenant_id)},
        )


# Import reset_profile at module level to avoid circular imports
from app.db.queries.users import reset_profile
