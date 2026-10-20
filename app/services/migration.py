"""Embedding model migration framework.

ARCHITECT §13.1–13.6, PR-07: Миграционный фреймворк embedding-моделей.

This module provides the MigrationService for:
- Blue-green model replacement
- Backfill worker for document embeddings
- Canary reindex with similarity checks
- Qdrant snapshot management
- Rollback capabilities

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-07.
"""

from app.services.migration import MigrationService

__all__ = ["MigrationService"]
