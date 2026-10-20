"""Integration tests for personalized search (PR-01).

Tests GDPR endpoints and personalized search with mocked embedding service.
Uses testcontainers for PostgreSQL and wired_app for FastAPI integration.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.schemas import SearchRequest


@pytest.mark.integration
async def test_gdpr_delete_endpoint(wired_app, async_redis):
    """Test GDPR delete endpoint resets user profile."""
    app, client = wired_app

    # Create a user (simulate user creation)
    user_id = uuid4()
    tenant_id = uuid4()

    # Mock personalization service to track calls
    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service.gdpr_delete = AsyncMock()
        mock_service_class.return_value = mock_service

        # Call GDPR delete endpoint
        response = await client.delete(f"/users/{user_id}")

        # Verify response
        assert response.status_code == 200
        assert response.json() == {"deleted": True}

        # Verify service was called
        mock_service.gdpr_delete.assert_called_once_with(user_id, user_id)


@pytest.mark.integration
async def test_gdpr_delete_endpoint_not_found(wired_app, async_redis):
    """Test GDPR delete endpoint returns 200 even for non-existent users (idempotent)."""
    app, client = wired_app

    user_id = uuid4()

    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service.gdpr_delete = AsyncMock()
        mock_service_class.return_value = mock_service

        # Call GDPR delete endpoint for non-existent user
        response = await client.delete(f"/users/{user_id}")

        # Verify response (idempotent 200)
        assert response.status_code == 200
        assert response.json() == {"deleted": True}

        # Service still called (implementation handles non-existent users gracefully)
        mock_service.gdpr_delete.assert_called_once_with(user_id, user_id)


@pytest.mark.integration
async def test_get_user_profile_endpoint(wired_app, async_redis):
    """Test user profile export endpoint."""
    app, client = wired_app

    user_id = uuid4()

    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service.get_profile_vector = AsyncMock(return_value=None)
        mock_service_class.return_value = mock_service

        # Call profile export endpoint
        response = await client.get(f"/users/{user_id}/profile")

        # Verify response structure
        assert response.status_code == 200
        data = response.json()
        assert "user_id" in data
        assert "click_count" in data
        assert "profile_version" in data
        assert "profile_vector" in data
        assert data["user_id"] == str(user_id)


@pytest.mark.integration
async def test_personalized_search_with_mocked_embeddings(wired_app, async_redis):
    """Test personalized search with mocked embedding service."""
    app, client = wired_app

    user_id = uuid4()
    tenant_id = uuid4()

    # Mock personalization service
    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()

        # Mock profile vector for established user
        profile_vector = [0.1] * 1024
        mock_service.get_profile_vector = AsyncMock(return_value=profile_vector)
        mock_service.personalize_scores = AsyncMock(
            return_value=[
                (uuid4(), 0.9),  # Reordered due to personalization
                (uuid4(), 0.8),
            ]
        )

        mock_service_class.return_value = mock_service

        # Mock embedding service to return deterministic vectors
        with patch("app.api.routes.search._get_embedding_service") as mock_get_emb:
            mock_emb_service = AsyncMock()

            # Return normalized vectors for re-embedding
            def mock_embed(texts):
                vectors = []
                for text in texts:
                    # Create deterministic vector based on text
                    vec = [0.2] * 1024  # Deterministic for test
                    vectors.append(vec)
                return vectors

            mock_emb_service.embed_texts = AsyncMock(side_effect=mock_embed)
            mock_get_emb.return_value = mock_emb_service

            # Call search with personalization
            search_request = SearchRequest(
                query="test query",
                tenant_id=tenant_id,
                user_id=user_id,
                personalize=True,
                explain=True,
            )

            response = await client.post("/search", json=search_request.dict())

            # Verify response
            assert response.status_code == 200
            data = response.json()
            assert "hits" in data
            assert "personalization" in data
            assert data["personalization"]["applied"] is True
            assert data["personalization"]["cold_start"] is False

            # Verify per-hit debug info when explain=True
            for hit in data["hits"]:
                assert "debug" in hit
                assert "personalized" in hit["debug"]
                assert hit["debug"]["personalized"] is True


@pytest.mark.integration
async def test_personalized_search_cold_start(wired_app, async_redis):
    """Test personalized search with cold-start user (no personalization applied)."""
    app, client = wired_app

    user_id = uuid4()
    tenant_id = uuid4()

    # Mock personalization service returning None (cold-start)
    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service.get_profile_vector = AsyncMock(return_value=None)  # Cold-start
        mock_service_class.return_value = mock_service

        # Call search with personalization for cold-start user
        search_request = SearchRequest(
            query="test query", tenant_id=tenant_id, user_id=user_id, personalize=True, explain=True
        )

        response = await client.post("/search", json=search_request.dict())

        # Verify response (no personalization applied)
        assert response.status_code == 200
        data = response.json()
        assert "personalization" in data
        assert data["personalization"]["applied"] is False
        assert data["personalization"]["cold_start"] is True

        # Verify per-hit debug info
        for hit in data["hits"]:
            assert "debug" in hit
            assert "personalized" in hit["debug"]
            assert hit["debug"]["personalized"] is False


@pytest.mark.integration
async def test_search_without_personalization(wired_app, async_redis):
    """Test search without personalization (baseline behavior unchanged)."""
    app, client = wired_app

    user_id = uuid4()
    tenant_id = uuid4()

    # Mock personalization service but don't use it
    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service_class.return_value = mock_service

        # Call search without personalization
        search_request = SearchRequest(
            query="test query",
            tenant_id=tenant_id,
            user_id=user_id,
            personalize=False,  # No personalization
            explain=False,
        )

        response = await client.post("/search", json=search_request.dict())

        # Verify response (no personalization debug info)
        assert response.status_code == 200
        data = response.json()
        assert "personalization" not in data  # Not present when not applied

        # Verify service was not called
        mock_service.get_profile_vector.assert_not_called()


@pytest.mark.integration
async def test_personalization_disabled_by_feature_flag(wired_app, async_redis):
    """Test search with personalization disabled by feature flag."""
    app, client = wired_app

    user_id = uuid4()
    tenant_id = uuid4()

    # Override feature flag to disable personalization
    app.dependency_overrides[app.state.settings.feature_flags.personalization_enabled] = False

    with patch("app.services.personalization.PersonalizationService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service_class.return_value = mock_service

        # Call search with personalization but flag disabled
        search_request = SearchRequest(
            query="test query", tenant_id=tenant_id, user_id=user_id, personalize=True, explain=True
        )

        response = await client.post("/search", json=search_request.dict())

        # Verify response (no personalization applied)
        assert response.status_code == 200
        data = response.json()
        assert "personalization" in data
        assert data["personalization"]["applied"] is False

        # Verify service was not called due to flag
        mock_service.get_profile_vector.assert_not_called()

    # Clear override
    app.dependency_overrides.clear()
