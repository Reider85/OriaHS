"""Query helpers for the ``user_context_profiles`` table (ARCHITECT §10.6, PR-05).

Contextual personalization queries: user profile management per context
(session, time-of-day, device). All functions are idempotent and handle
cold-start gracefully.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.db.models.user_context_profile import UserContextProfile

from app.db.models.user_context_profile import UserContextProfile


async def get_profile(
    session: AsyncSession, user_id: UUID, context_key: str
) -> UserContextProfile | None:
    """Fetch a context profile by user_id and context_key, returns None if not found."""
    result = await session.execute(
        text(
            """
            SELECT * FROM user_context_profiles
            WHERE user_id = :user_id AND context_key = :context_key
            """
        ),
        {"user_id": user_id, "context_key": context_key},
    )
    row = result.fetchone()
    if row is None:
        return None

    # Convert row to UserContextProfile instance (simplified ORM-style)
    return UserContextProfile(
        user_id=row.user_id,
        context_key=row.context_key,
        profile_vector=row.profile_vector,
        click_count=row.click_count,
        profile_version=row.profile_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def upsert_profile(
    session: AsyncSession, user_id: UUID, context_key: str, profile: list[float]
) -> None:
    """Upsert a context profile vector with EMA metadata.

    If profile exists: update vector, increment version and click count.
    If new: insert with version 1, click count 1.

    Args:
        session: Async session
        user_id: User identifier
        context_key: Context key (e.g., "session:abc123", "morning")
        profile: New profile vector (1024-dim, L2-normalized)
    """
    await session.execute(
        text(
            """
            INSERT INTO user_context_profiles (user_id, context_key, profile_vector, click_count, profile_version, created_at, updated_at)
            VALUES (:user_id, :context_key, :profile, 1, 1, now(), now())
            ON CONFLICT (user_id, context_key) DO UPDATE SET
                profile_vector = :profile,
                click_count = user_context_profiles.click_count + 1,
                profile_version = user_context_profiles.profile_version + 1,
                updated_at = now()
            """
        ),
        {"user_id": user_id, "context_key": context_key, "profile": profile},
    )


async def delete_for_user(session: AsyncSession, user_id: UUID) -> int:
    """Delete all context profiles for a user (GDPR compliance).

    Args:
        session: Async session
        user_id: User identifier

    Returns:
        Number of deleted rows
    """
    result = await session.execute(
        text("DELETE FROM user_context_profiles WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    return cast(CursorResult[Any], result).rowcount or 0


async def expire_session_profiles(session: AsyncSession, cutoff: datetime) -> int:
    """Delete expired session profiles (context_key LIKE 'session:%').

    Args:
        session: Async session
        cutoff: Datetime threshold — profiles updated before this are expired

    Returns:
        Number of deleted session profiles
    """
    result = await session.execute(
        text(
            """
            DELETE FROM user_context_profiles
            WHERE context_key LIKE 'session:%' AND updated_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    return cast(CursorResult[Any], result).rowcount or 0
