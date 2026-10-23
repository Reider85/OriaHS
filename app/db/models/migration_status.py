"""Migration status enumeration for model migration framework (PR-07)."""

from enum import Enum


class MigrationStatus(str, Enum):
    """Migration lifecycle states."""

    INIT = "init"
    CANARY_PASSED = "canary_passed"
    CANARY_FAILED = "canary_failed"
    BACKFILLED = "backfilled"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    CLEANED = "cleaned"


class MigrationPhase(str, Enum):
    """Migration phases for CLI and orchestration."""

    INIT = "init"
    CANARY = "canary"
    BACKFILL = "backfill"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    CLEANUP = "cleanup"
    FULL_REINDEX = "full_reindex"
