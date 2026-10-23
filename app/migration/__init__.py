"""Migration framework package for embedding model migrations (PR-07).

Provides blue-green deployment, backfill worker, canary reindex,
Qdrant snapshot management, and rollback capabilities.
"""

# Lazy imports to avoid circular import issues
from app.services.migration import MigrationService

__all__ = ["MigrationService"]
