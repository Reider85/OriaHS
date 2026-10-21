"""Personalization service for user profile vectors and EMA updates.

ARCHITECT §10.1–10.6, PR-01/PR-05: User profile vector (EMA, cold-start, GDPR),
contextual personalization (session, time-of-day, device).

This module provides the PersonalizationService for:
- User profile vector management (aggregated embeddings)
- Contextual profile management (session, time-of-day, device)
- EMA updates on click events (default + contextual)
- Personalized re-ranking of search results
- GDPR compliance (export/delete)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import numpy as np
from numpy.linalg import norm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchContext
from app.config import PersonalizationConfig
from app.db.queries.user_context_profiles import (
    delete_for_user,
    expire_session_profiles,
    get_profile,
    upsert_profile,
)
from app.db.queries.users import (
    get_by_id,
    get_or_create,
    increment_click_count,
    reset_profile,
    update_profile_vector,
)
from app.embedding.service import EmbeddingService
from app.observability import metrics

logger = logging.getLogger(__name__)


@dataclass
class ProfileInfo:
    """Profile vector and metadata for personalized search.

    Attributes:
        vector: 1024-dim L2-normalized profile vector, or None for cold-start
        profile_version: Version counter for cache invalidation
        click_count: Number of clicks (for alpha selection)
        context_key: Context key used ("default", "morning", "desktop", "session:abc123")
    """

    vector: np.ndarray | None
    profile_version: int
    click_count: int
    context_key: str


class PersonalizationService:
    """Service for user profile management and personalized search re-ranking.

    Implements EMA aggregation of clicked document embeddings with adaptive alpha:
    - α = 0.5 for users with < 10 clicks (fast learning)
    - α = 0.05 for established users (slow adaptation)
    - Cold-start: no profile → pure fusion search
    - GDPR: profile reset (null vector, zero counters, preserve audit record)
    - Contextual personalization: session (α=0.5), time-of-day/device (α=0.1)
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
        profile_vector: np.ndarray | None = None,
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
        if profile_vector is None:
            logger.debug(
                "Cold-start user, no personalization applied", extra={"user_id": str(user_id)}
            )
            return fused_hits

        # Emit personalized search metric
        metrics.personalized_searches_total.labels(
            tenant_id=str(tenant_id), cold_start="false"
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
            cosine_sim = float(np.dot(profile_vector, doc_vector))

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
        # Delete all context profiles for GDPR compliance
        await delete_for_user(self._session, user_id)
        logger.info(
            "User profile GDPR reset (including context profiles)",
            extra={"user_id": str(user_id), "tenant_id": str(tenant_id)},
        )

    # Contextual personalization methods (PR-05)
    @staticmethod
    def resolve_context_key(context: "SearchContext | None") -> str | None:
        """Resolve context key from SearchContext with priority.

        Priority: session_id > time_of_day > device > None.
        Returns context key string or None if context is None.

        Args:
            context: SearchContext from request

        Returns:
            Context key ("session:abc123", "time-of-day:morning", "device:mobile", or None)
        """
        if context is None:
            return None
        if context.session_id is not None:
            return f"session:{context.session_id}"
        if context.time_of_day is not None:
            return f"time-of-day:{context.time_of_day}"
        if context.device is not None:
            return f"device:{context.device}"
        return None

    @staticmethod
    def time_of_day_key(now: datetime) -> Literal["morning", "afternoon", "evening", "night"]:
        """Get time-of-day bucket from datetime in configured timezone.

        Buckets (UTC):
        - night: 00:00–06:00
        - morning: 06:00–12:00
        - afternoon: 12:00–18:00
        - evening: 18:00–24:00

        Args:
            now: Datetime to classify

        Returns:
            Time-of-day bucket string
        """
        hour = now.hour
        if 6 <= hour < 12:
            return "morning"
        elif 12 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 24:
            return "evening"
        else:
            return "night"

    async def get_profile_info(
        self, user_id: UUID, tenant_id: UUID, context: "SearchContext | None"
    ) -> ProfileInfo:
        """Get user profile info with contextual resolution and fallback.

        Priority: context > default. Context cold-start falls back to default.
        Default cold-start returns vector=None.

        Args:
            user_id: User identifier
            tenant_id: Tenant identifier (for audit logging)
            context: SearchContext from request (may be None)

        Returns:
            ProfileInfo with vector, version, click_count, context_key
        """
        # Try context-specific profile if context provided and flag would be on
        context_key = self.resolve_context_key(context)
        if context_key is not None:
            # Get context-specific profile
            context_profile = await get_profile(self._session, user_id, context_key)
            if (
                context_profile is not None
                and context_profile.profile_vector is not None
                and context_profile.click_count > 0
            ):
                # Context profile exists and has clicks → use it
                metrics.contextual_profile_used_total.labels(context_key=context_key).inc()
                return ProfileInfo(
                    vector=np.array(context_profile.profile_vector, dtype=np.float32),
                    profile_version=context_profile.profile_version,
                    click_count=context_profile.click_count,
                    context_key=context_key,
                )
            else:
                # Context profile cold-start → fallback to default
                logger.debug(
                    "Context profile cold-start, falling back to default",
                    extra={"user_id": str(user_id), "context_key": context_key},
                )
                context_key = "default"

        # Use default profile (either no context or context fallback)
        user = await get_by_id(self._session, user_id)
        if user is None or user.profile_vector is None or user.click_count == 0:
            # Default cold-start
            return ProfileInfo(
                vector=None,
                profile_version=0,
                click_count=0,
                context_key="default",
            )

        # Default profile exists
        return ProfileInfo(
            vector=np.array(user.profile_vector, dtype=np.float32),
            profile_version=user.profile_version,
            click_count=user.click_count,
            context_key="default",
        )

    async def update_on_click_contextual(
        self,
        user_id: UUID,
        tenant_id: UUID,
        clicked_doc_id: UUID,
        embedding_service: EmbeddingService,
        context: "SearchContext | None",
    ) -> str:
        """Update user's contextual profile with EMA aggregation on click.

        Adaptive alpha: 0.5 for session context, 0.1 for time-of-day/device.
        Placeholder embedding used (deferred document embedding, PR-01 pattern).

        Args:
            user_id: User identifier
            tenant_id: Tenant identifier (for audit logging)
            clicked_doc_id: Document that was clicked
            embedding_service: Embedding service (currently unused - placeholder)
            context: SearchContext from request

        Returns:
            Context key that was updated
        """
        # Resolve context key
        context_key = self.resolve_context_key(context)
        if context_key is None:
            raise ValueError("Context cannot be None for contextual update")

        # Get context-specific profile or create cold-start
        context_profile = await get_profile(self._session, user_id, context_key)
        old_profile = (
            np.array(context_profile.profile_vector, dtype=np.float32)
            if context_profile is not None and context_profile.profile_vector is not None
            else None
        )

        # Select alpha based on context type
        if context_key.startswith("session:"):
            alpha = self._config.session_profile_alpha
        else:
            alpha = self._config.context_profile_alpha

        # EMA update: new_profile = α * clicked + (1-α) * old_profile
        if old_profile is not None:
            # TODO: Replace with actual document embedding when document queries available
            # For now, use a placeholder vector (will be replaced in PR-02)
            clicked_vector = np.random.randn(1024).astype(np.float32)  # Placeholder
            clicked_vector = clicked_vector / norm(clicked_vector)  # L2 normalize

            new_profile = alpha * clicked_vector + (1 - alpha) * old_profile
            # Renormalize after EMA (required for cosine similarity to work)
            new_profile = new_profile / norm(new_profile)

            await upsert_profile(self._session, user_id, context_key, new_profile.tolist())
        else:
            # First click: use clicked_vector directly (already normalized)
            # TODO: Replace with actual document embedding
            clicked_vector = np.random.randn(1024).astype(np.float32)
            clicked_vector = clicked_vector / norm(clicked_vector)
            await upsert_profile(self._session, user_id, context_key, clicked_vector.tolist())

        # Emit metrics and log update for observability
        metrics.profile_vector_updates_total.labels(tenant_id=str(tenant_id)).inc()
        logger.info(
            "Contextual profile updated",
            extra={
                "user_id": str(user_id),
                "tenant_id": str(tenant_id),
                "context_key": context_key,
                "alpha": alpha,
                "profile_version": context_profile.profile_version + 1
                if context_profile is not None
                else 1,
            },
        )

        return context_key

    async def expire_session_profiles(self, session_ttl_hours: int | None = None) -> int:
        """Delete expired session profiles (context_key LIKE 'session:%').

        Args:
            session_ttl_hours: TTL in hours (uses config default if None)

        Returns:
            Number of deleted session profiles
        """
        ttl = session_ttl_hours or self._config.session_profile_ttl_hours
        cutoff = datetime.now(ZoneInfo(self._config.context_timezone)) - timedelta(hours=ttl)
        deleted_count = await expire_session_profiles(self._session, cutoff)
        metrics.session_profiles_expired_total.inc(deleted_count)

        logger.info(
            "Session profiles expired",
            extra={
                "deleted_count": deleted_count,
                "cutoff": cutoff.isoformat(),
                "ttl_hours": ttl,
            },
        )

        return deleted_count
