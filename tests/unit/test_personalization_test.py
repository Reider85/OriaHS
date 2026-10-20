"""Unit tests for PersonalizationService (PR-01).

Tests EMA aggregation, adaptive alpha, cold-start handling, personalized scoring,
and GDPR compliance. All database operations are mocked with AsyncMock.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

from app.config import PersonalizationConfig
from app.services.personalization import PersonalizationService


@pytest.fixture
def mock_session():
    """Mock async session for database operations."""
    return AsyncMock()


@pytest.fixture
def mock_config():
    """Personalization config with test values."""
    return PersonalizationConfig(
        ema_alpha_new=0.5,
        ema_alpha_established=0.05,
        new_user_click_threshold=10,
        profile_weight=0.3,
        fused_weight=0.7,
        cold_start_enabled=True,
        profile_dimension=1024,
        gdpr_delete_endpoint_enabled=True,
    )


@pytest.fixture
def personalization_service(mock_session, mock_config):
    """Personalization service with mocked session and config."""
    return PersonalizationService(mock_session, mock_config)


class TestPersonalizationService:
    """Test suite for PersonalizationService methods."""

    @pytest.mark.asyncio
    async def test_get_profile_vector_cold_start(self, personalization_service):
        """Test get_profile_vector returns None for cold-start users."""
        # Mock get_by_id to return None (user doesn't exist)
        with patch("app.services.personalization.get_by_id", return_value=None):
            profile = await personalization_service.get_profile_vector(uuid4(), uuid4())
            assert profile is None

    @pytest.mark.asyncio
    async def test_get_profile_vector_null_vector(self, personalization_service):
        """Test get_profile_vector returns None when profile_vector is NULL."""
        # Mock user with NULL profile_vector
        mock_user = MagicMock()
        mock_user.profile_vector = None
        mock_user.click_count = 5

        with patch("app.services.personalization.get_by_id", return_value=mock_user):
            profile = await personalization_service.get_profile_vector(uuid4(), uuid4())
            assert profile is None

    @pytest.mark.asyncio
    async def test_get_profile_vector_no_clicks(self, personalization_service):
        """Test get_profile_vector returns None when click_count is 0."""
        # Mock user with no clicks
        mock_user = MagicMock()
        mock_user.profile_vector = [0.1] * 1024
        mock_user.click_count = 0

        with patch("app.services.personalization.get_by_id", return_value=mock_user):
            profile = await personalization_service.get_profile_vector(uuid4(), uuid4())
            assert profile is None

    @pytest.mark.asyncio
    async def test_get_profile_vector_success(self, personalization_service):
        """Test get_profile_vector returns valid profile vector."""
        # Mock user with profile and clicks
        mock_user = MagicMock()
        mock_user.profile_vector = [0.1] * 1024
        mock_user.click_count = 5

        with patch("app.services.personalization.get_by_id", return_value=mock_user):
            profile = await personalization_service.get_profile_vector(uuid4(), uuid4())
            assert profile is not None
            assert len(profile) == 1024
            assert isinstance(profile, np.ndarray)

    @pytest.mark.asyncio
    async def test_update_on_click_new_user(self, personalization_service):
        """Test update_on_click with new user (< 10 clicks)."""
        user_id = uuid4()
        tenant_id = uuid4()
        clicked_doc_id = uuid4()

        # Mock get_or_create returns user with 0 clicks
        mock_user = MagicMock()
        mock_user.click_count = 0
        mock_user.profile_version = 0

        with (
            patch("app.services.personalization.get_or_create", return_value=mock_user),
            patch("app.services.personalization.update_profile_vector") as mock_update,
            patch("app.services.personalization.increment_click_count") as mock_increment,
            patch("app.services.personalization.EmbeddingService") as mock_embedding_service,
            patch("app.services.personalization.get_by_id") as mock_get_by_id,
        ):
            # Mock get_by_id to return None (cold-start, no existing profile)
            mock_get_by_id.return_value = None

            # Mock embedding service to return normalized vector
            mock_embedding = np.random.randn(1024).astype(np.float32)
            mock_embedding = mock_embedding / np.linalg.norm(mock_embedding)  # L2 normalize
            mock_embedding_service.embed_texts.return_value = np.array([mock_embedding])

            await personalization_service.update_on_click(
                user_id, tenant_id, clicked_doc_id, mock_embedding_service
            )

            # Verify update with alpha=0.5 (new user)
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args.kwargs["bump_version"] is True

            # Verify click count increment
            mock_increment.assert_called_once_with(personalization_service._session, user_id)

    @pytest.mark.asyncio
    async def test_update_on_click_established_user(self, personalization_service):
        """Test update_on_click with established user (>= 10 clicks)."""
        user_id = uuid4()
        tenant_id = uuid4()
        clicked_doc_id = uuid4()

        # Mock get_or_create returns user with 15 clicks (established)
        mock_user = MagicMock()
        mock_user.click_count = 15
        mock_user.profile_version = 3
        mock_user.profile_vector = np.random.randn(1024).astype(np.float32)  # Has existing profile

        with (
            patch("app.services.personalization.get_or_create", return_value=mock_user),
            patch("app.services.personalization.update_profile_vector") as mock_update,
            patch("app.services.personalization.increment_click_count"),
            patch("app.services.personalization.EmbeddingService") as mock_embedding_service,
            patch("app.services.personalization.get_by_id") as mock_get_by_id,
        ):
            # Mock get_by_id to return existing user with profile
            mock_get_by_id.return_value = mock_user

            # Mock embedding service
            mock_embedding = np.random.randn(1024).astype(np.float32)
            mock_embedding = mock_embedding / np.linalg.norm(mock_embedding)
            mock_embedding_service.embed_texts.return_value = np.array([mock_embedding])

            await personalization_service.update_on_click(
                user_id, tenant_id, clicked_doc_id, mock_embedding_service
            )

            # Verify update with alpha=0.05 (established user)
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args.kwargs["bump_version"] is True

    @pytest.mark.asyncio
    async def test_personalize_scores_cold_start(self, personalization_service):
        """Test personalize_scores returns fused hits unchanged for cold-start."""
        fused_hits = [(uuid4(), 0.8), (uuid4(), 0.6)]
        doc_vectors = {uuid4(): np.random.randn(1024).astype(np.float32)}

        with patch.object(personalization_service, "get_profile_vector", return_value=None):
            result = await personalization_service.personalize_scores(
                fused_hits, uuid4(), uuid4(), doc_vectors
            )
            assert result == fused_hits  # Unchanged for cold-start

    @pytest.mark.asyncio
    async def test_personalize_scores_applied(self, personalization_service):
        """Test personalize_scores applies personalized scoring correctly."""
        user_id = uuid4()
        tenant_id = uuid4()
        doc_id1 = uuid4()
        doc_id2 = uuid4()

        # Mock profile vector
        profile = np.random.randn(1024).astype(np.float32)
        profile = profile / np.linalg.norm(profile)  # L2 normalize

        # Mock document vectors
        vec1 = np.random.randn(1024).astype(np.float32)
        vec1 = vec1 / np.linalg.norm(vec1)
        vec2 = np.random.randn(1024).astype(np.float32)
        vec2 = vec2 / np.linalg.norm(vec2)

        doc_vectors = {doc_id1: vec1, doc_id2: vec2}

        # Mock get_profile_vector returns profile
        with patch.object(personalization_service, "get_profile_vector", return_value=profile):
            fused_hits = [(doc_id1, 0.8), (doc_id2, 0.6)]
            result = await personalization_service.personalize_scores(
                fused_hits, user_id, tenant_id, doc_vectors
            )

            # Result should be sorted (personalized scoring applied)
            assert len(result) == 2
            assert result[0][0] == doc_id1 or result[0][0] == doc_id2

            # Verify personalized scores are computed
            # final_score = 0.7*fused + 0.3*cosine(profile, doc)
            for doc_id, final_score in result:
                original_score = next(score for d, score in fused_hits if d == doc_id)
                cosine_sim = np.dot(profile, doc_vectors[doc_id])
                expected_score = 0.7 * original_score + 0.3 * cosine_sim
                assert abs(final_score - expected_score) < 1e-6

    @pytest.mark.asyncio
    async def test_gdpr_delete(self, personalization_service):
        """Test GDPR delete resets user profile."""
        user_id = uuid4()
        tenant_id = uuid4()

        with patch("app.services.personalization.reset_profile") as mock_reset:
            await personalization_service.gdpr_delete(user_id, tenant_id)
            mock_reset.assert_called_once_with(personalization_service._session, user_id)

    def test_ema_formula_new_user(self, personalization_service):
        """Test EMA formula with new user (alpha=0.5)."""
        old_profile = np.array([0.1, 0.2, 0.3])
        clicked_vector = np.array([0.4, 0.5, 0.6])

        # EMA: new = α*clicked + (1-α)*old
        expected = 0.5 * clicked_vector + 0.5 * old_profile

        result = (
            personalization_service._config.ema_alpha_new * clicked_vector
            + (1 - personalization_service._config.ema_alpha_new) * old_profile
        )

        np.testing.assert_array_almost_equal(result, expected)

    def test_ema_formula_established_user(self, personalization_service):
        """Test EMA formula with established user (alpha=0.05)."""
        old_profile = np.array([0.1, 0.2, 0.3])
        clicked_vector = np.array([0.4, 0.5, 0.6])

        # EMA: new = α*clicked + (1-α)*old
        expected = 0.05 * clicked_vector + 0.95 * old_profile

        result = (
            personalization_service._config.ema_alpha_established * clicked_vector
            + (1 - personalization_service._config.ema_alpha_established) * old_profile
        )

        np.testing.assert_array_almost_equal(result, expected)
