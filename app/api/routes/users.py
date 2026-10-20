"""User management endpoints (ARCHITECT §10.4, PR-01).

GDPR compliance: user profile export and deletion endpoints.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_personalization_service, get_session
from app.services.personalization import PersonalizationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


SessionDep = Annotated[AsyncSession, Depends(get_session)]
PersonalizationDep = Annotated[PersonalizationService, Depends(get_personalization_service)]


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
async def delete_user(
    user_id: UUID,
    session: SessionDep,
    personalization_service: PersonalizationDep,
) -> dict[str, bool]:
    """Delete user profile for GDPR compliance (PR-01).

    Resets profile_vector to NULL, click_count to 0, profile_version to 0.
    Preserves user record for audit purposes.

    Args:
        user_id: User identifier
        session: Database session
        personalization_service: Personalization service for GDPR reset

    Returns:
        {"deleted": true} (always true, idempotent operation)
    """
    await personalization_service.gdpr_delete(user_id, user_id)  # TODO: tenant_id needed?
    return dict[str, bool]


@router.get("/{user_id}/profile")
async def get_user_profile(
    user_id: UUID,
    session: SessionDep,
    personalization_service: PersonalizationDep,
) -> dict:
    """Export user profile for GDPR right to access (PR-01).

    Returns user profile information including click history and profile vector.

    Args:
        user_id: User identifier
        session: Database session
        personalization_service: Personalization service for profile export

    Returns:
        {
            "user_id": UUID,
            "click_count": int,
            "profile_version": int,
            "profile_vector": list[float] | None
        }

    Raises:
        HTTP 404: If user does not exist
    """
    # TODO: Need to implement profile export method in PersonalizationService
    # For now, return placeholder structure
    # profile_vector = await personalization_service.get_profile_vector(user_id, user_id)

    return {
        "user_id": str(user_id),
        "click_count": 0,  # TODO: get from service
        "profile_version": 0,  # TODO: get from service
        "profile_vector": None,  # TODO: get from service
    }
