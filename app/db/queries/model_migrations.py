"""Database queries for model migration framework (PR-07).

Provides queries for migration lifecycle management, dual-write targets,
and active search target resolution.
"""

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from app.config import settings
from app.db.models.model_migration import MigrationStatus, ModelMigration
from app.db.session import Session
from app.observability.metrics import migration_backfill_progress, migration_backfill_speed

if TYPE_CHECKING:
    from app.db.models.model_migration import ModelMigration


def create_migration(
    session: Session,
    old_model_name: str,
    new_model_name: str,
    old_collection: str,
    new_collection: str,
    new_hf_model: str,
) -> ModelMigration:
    """Create a new migration record."""
    migration = ModelMigration(
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        old_collection=old_collection,
        new_collection=new_collection,
        new_hf_model=new_hf_model,
        status=MigrationStatus.INIT,
    )
    session.add(migration)
    session.commit()
    session.refresh(migration)
    return migration


def get_migration(session: Session, migration_id: uuid.UUID) -> ModelMigration | None:
    """Get a migration by ID."""
    return session.get(ModelMigration, migration_id)


def get_latest_promoted(session: Session) -> ModelMigration | None:
    """Get the latest promoted migration for dual-write and search switching."""
    return session.scalars(
        select(ModelMigration)
        .where(ModelMigration.status == MigrationStatus.PROMOTED)
        .order_by(ModelMigration.promoted_at.desc())
        .limit(1)
    ).first()


def get_dual_write_targets(session: Session) -> list[tuple[str, str]] | None:
    """Get dual-write targets if a promoted migration exists.
    
    Returns list of (collection, model_name) tuples for dual-write.
    Returns None if no promoted migration exists.
    """
    promoted = get_latest_promoted(session)
    if not promoted:
        return None

    return [
        (promoted.old_collection, promoted.old_model_name),
        (promoted.new_collection, promoted.new_model_name),
    ]


# Cache for active search target (30s TTL to avoid DB hits on every request)
_search_target_cache: dict[str, tuple[str, str]] = {}
_search_target_cache_time: dict[str, datetime] = {}


def get_active_search_target(session: Session, force_refresh: bool = False) -> tuple[str, str]:
    """Get active search target (collection, model_name).
    
    If a promoted migration exists, returns new collection and model.
    Otherwise, returns default from settings.
    
    Uses 30s TTL cache to avoid DB hits on every request.
    """
    cache_key = "active_search_target"
    now = datetime.now()

    # Check cache first (if not forced refresh and cache is fresh)
    if (
        not force_refresh
        and cache_key in _search_target_cache
        and (now - _search_target_cache_time[cache_key]).total_seconds() < 30
    ):
        return _search_target_cache[cache_key]

    try:
        promoted = get_latest_promoted(session)
        if promoted:
            result = (promoted.new_collection, promoted.new_model_name)
        else:
            # Fallback to default settings
            result = (
                settings.qdrant.collection,
                settings.embedding.model_name,
            )

        # Update cache
        _search_target_cache[cache_key] = result
        _search_target_cache_time[cache_key] = now

        return result

    except Exception:
        # Any DB error → fallback to settings (fail-open)
        return (
            settings.qdrant.collection,
            settings.embedding.model_name,
        )


def set_status(
    session: Session,
    migration_id: uuid.UUID,
    status: MigrationStatus,
    **kwargs,
) -> None:
    """Set migration status and optional additional fields."""
    update_stmt = (
        update(ModelMigration)
        .where(ModelMigration.id == migration_id)
        .values(
            status=status,
            updated_at=func.now(),
            **kwargs,
        )
    )
    session.execute(update_stmt)
    session.commit()


def update_canary_results(
    session: Session,
    migration_id: uuid.UUID,
    avg_similarity: float,
    min_similarity: float,
    max_similarity: float,
    passed: bool,
) -> None:
    """Update canary similarity results."""
    set_status(
        session,
        migration_id,
        MigrationStatus.CANARY_PASSED if passed else MigrationStatus.CANARY_FAILED,
        canary_avg_similarity=avg_similarity,
        canary_min_similarity=min_similarity,
        canary_max_similarity=max_similarity,
        canary_passed=passed,
    )


def update_backfill_progress(
    session: Session,
    migration_id: uuid.UUID,
    processed: int,
    total: int,
) -> None:
    """Update backfill progress and metrics."""
    migration = get_migration(session, migration_id)
    if not migration:
        return

    old_processed = migration.backfill_processed
    migration.backfill_processed = processed
    migration.backfill_total = total

    # Update progress metric
    migration_backfill_progress.set(
        processed,
        labels={"migration_id": str(migration_id)}
    )

    # Update speed metric if we have progress
    if processed > 0 and old_processed < processed:
        elapsed = (datetime.now() - migration.created_at).total_seconds()
        if elapsed > 0:
            speed = (processed - old_processed) / elapsed * 3600  # docs/hour
            migration_backfill_speed.set(
                speed,
                labels={"migration_id": str(migration_id)}
            )

    session.commit()


def mark_backfilled(session: Session, migration_id: uuid.UUID) -> None:
    """Mark migration as backfilled."""
    set_status(session, migration_id, MigrationStatus.BACKFILLED)


def mark_promoted(session: Session, migration_id: uuid.UUID) -> None:
    """Mark migration as promoted and set promoted_at timestamp."""
    set_status(
        session,
        migration_id,
        MigrationStatus.PROMOTED,
        promoted_at=func.now(),
    )


def mark_rolled_back(session: Session, migration_id: uuid.UUID) -> None:
    """Mark migration as rolled back."""
    set_status(session, migration_id, MigrationStatus.ROLLED_BACK)


def mark_cleaned(session: Session, migration_id: uuid.UUID) -> None:
    """Mark migration as cleaned and set cleaned_at timestamp."""
    set_status(
        session,
        migration_id,
        MigrationStatus.CLEANED,
        cleaned_at=func.now(),
    )


def set_snapshot_s3_key(session: Session, migration_id: uuid.UUID, s3_key: str) -> None:
    """Set S3 snapshot key for the migration."""
    migration = get_migration(session, migration_id)
    if migration:
        migration.snapshot_s3_key = s3_key
        session.commit()


def list_migrations(
    session: Session,
    limit: int = 50,
    status: MigrationStatus | None = None
) -> list[ModelMigration]:
    """List migrations with optional status filter."""
    query = select(ModelMigration).order_by(ModelMigration.created_at.desc())

    if status:
        query = query.where(ModelMigration.status == status)

    return session.scalars(query.limit(limit)).all()


def is_cleanup_allowed(session: Session, migration_id: uuid.UUID, force: bool = False) -> bool:
    """Check if cleanup is allowed for a migration.
    
    Cleanup is allowed if:
    - Migration is promoted AND (force OR promoted_at + stable_days <= now)
    """
    migration = get_migration(session, migration_id)
    if not migration or migration.status != MigrationStatus.PROMOTED:
        return False

    if force:
        return True

    if migration.promoted_at:
        stable_period = timedelta(days=settings.migration.stable_days)
        return datetime.now() >= migration.promoted_at + stable_period

    return False
