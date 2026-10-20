"""Query helpers for the ``users`` table (ARCHITECT §10.1, PR-01).

Personalization queries: user profile management (EMA vectors, click tracking,
GDPR compliance). All functions are idempotent and handle cold-start gracefully.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.db.models.user import User

from app.db.models.user import User


async def get_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    """Fetch a user by ID, returns None if not found."""
    result = await session.execute(
        text("SELECT * FROM users WHERE id = :user_id"), {"user_id": user_id}
    )
    row = result.fetchone()
    if row is None:
        return None

    # Convert row to User instance (simplified ORM-style for consistency)
    return User(
        id=row.id,
        tenant_id=row.tenant_id,
        profile_vector=row.profile_vector,
        profile_version=row.profile_version,
        click_count=row.click_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def get_or_create(session: AsyncSession, user_id: UUID, tenant_id: UUID) -> User:
    """Get existing user or create a new one with default values."""
    user = await get_by_id(session, user_id)
    if user is not None:
        return user

    # Create new user with default profile (NULL vector, version 0, click_count 0)
    await session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, profile_vector, profile_version, click_count, created_at, updated_at)
            VALUES (:user_id, :tenant_id, NULL, 0, 0, now(), now())
            """
        ),
        {"user_id": user_id, "tenant_id": tenant_id},
    )

    # Return the created user
    return await get_by_id(session, user_id)


async def update_profile_vector(
    session: AsyncSession, user_id: UUID, profile: list[float], *, bump_version: bool = True
) -> None:
    """Update user's profile_vector with new EMA-aggregated vector.

    Args:
        session: Async session
        user_id: User identifier
        profile: New profile vector (1024-dim, L2-normalized)
        bump_version: If True, increment profile_version (default: True)
    """
    if bump_version:
        await session.execute(
            text(
                """
                UPDATE users
                SET profile_vector = :profile,
                    profile_version = profile_version + 1,
                    updated_at = now()
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id, "profile": profile},
        )
    else:
        await session.execute(
            text(
                """
                UPDATE users
                SET profile_vector = :profile,
                    updated_at = now()
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id, "profile": profile},
        )


async def increment_click_count(session: AsyncSession, user_id: UUID) -> None:
    """Increment user's click_count (used for adaptive alpha selection)."""
    await session.execute(
        text(
            """
            UPDATE users
            SET click_count = click_count + 1,
                updated_at = now()
            WHERE id = :user_id
            """
        ),
        {"user_id": user_id},
    )


async def reset_profile(session: AsyncSession, user_id: UUID) -> None:
    """Reset user profile (GDPR compliance): null vector, zero counters.

    Preserves user record for audit purposes; call returns 200 even if user
    doesn't exist (idempotent GDPR delete).
    """
    await session.execute(
        text(
            """
            UPDATE users
            SET profile_vector = NULL,
                profile_version = 0,
                click_count = 0,
                updated_at = now()
            WHERE id = :user_id
            """
        ),
        {"user_id": user_id},
    )
