"""Migration CLI for embedding model migrations (PR-07).

Provides command-line interface for managing migrations:
- init: Initialize new migration
- canary: Run canary reindex
- snapshot: Create Qdrant snapshot
- backfill: Backfill documents with new model
- promote: Promote migration to active
- rollback: Rollback migration
- cleanup: Clean up old collection
- status: Show migration status
- full-reindex: Full reindex from PostgreSQL
"""

import argparse
import asyncio
import json
import sys

import structlog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.migration import MigrationService
from app.observability.logging import setup_logging
from app.services.qdrant import QdrantService

logger = structlog.get_logger("migration_cli")


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="Embedding model migration CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Migration commands")

    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize new migration")
    init_parser.add_argument(
        "--model",
        required=True,
        help="New model name (e.g., bge-m3-v2)"
    )
    init_parser.add_argument(
        "--dimension",
        type=int,
        required=True,
        help="New model dimension"
    )
    init_parser.add_argument(
        "--hf-model",
        help="HF model ID (default: model name)"
    )

    # Canary command
    canary_parser = subparsers.add_parser("canary", help="Run canary reindex")
    canary_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )
    canary_parser.add_argument(
        "--percentage",
        type=float,
        default=0.01,
        help="Percentage of documents to sample (default: 0.01)"
    )

    # Snapshot command
    snapshot_parser = subparsers.add_parser("snapshot", help="Create Qdrant snapshot")
    snapshot_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )
    snapshot_parser.add_argument(
        "--collection",
        help="Collection name (default: migration's new collection)"
    )

    # Backfill command
    backfill_parser = subparsers.add_parser("backfill", help="Backfill documents")
    backfill_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )
    backfill_parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for processing (default: 50)"
    )

    # Promote command
    promote_parser = subparsers.add_parser("promote", help="Promote migration")
    promote_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )

    # Rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback migration")
    rollback_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )
    rollback_parser.add_argument(
        "--restore-snapshot",
        action="store_true",
        help="Restore snapshot during rollback"
    )

    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up old collection")
    cleanup_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )
    cleanup_parser.add_argument(
        "--force",
        action="store_true",
        help="Force cleanup regardless of stable period"
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Show migration status")
    status_parser.add_argument(
        "migration_id",
        help="Migration ID"
    )

    # Full reindex command
    reindex_parser = subparsers.add_parser("full-reindex", help="Full reindex from PostgreSQL")
    reindex_parser.add_argument(
        "--collection",
        help="Collection name (default: default collection)"
    )

    return parser


async def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Setup logging
    setup_logging()

    # Create database session
    engine = create_engine(str(settings.database_url))
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    try:
        # Create migration service
        qdrant_client = QdrantService(
            config=settings.qdrant,
            client=None,  # Will be created by MigrationService
        )

        migration_service = MigrationService(
            session=session,
            qdrant_client=qdrant_client,
        )

        # Execute command
        if args.command == "init":
            result = await migration_service.init_migration(
                new_model_name=args.model,
                new_dimension=args.dimension,
                new_hf_model=args.hf_model,
            )
            print(json.dumps({"migration_id": result}))
            return 0

        elif args.command == "canary":
            result = await migration_service.canary_reindex(
                args.migration_id,
                args.percentage,
            )
            print(json.dumps(result))
            return 0 if result.get("passed", False) else 1

        elif args.command == "snapshot":
            result = await migration_service.snapshot_qdrant(
                args.migration_id,
                args.collection,
            )
            print(json.dumps({"s3_key": result}))
            return 0

        elif args.command == "backfill":
            result = await migration_service.backfill(
                args.migration_id,
                args.batch_size,
            )
            print(json.dumps(result))
            return 0

        elif args.command == "promote":
            await migration_service.promote(args.migration_id)
            print(json.dumps({"status": "promoted"}))
            return 0

        elif args.command == "rollback":
            await migration_service.rollback(
                args.migration_id,
                args.restore_snapshot,
            )
            print(json.dumps({"status": "rolled_back"}))
            return 0

        elif args.command == "cleanup":
            await migration_service.cleanup_old_collection(
                args.migration_id,
                args.force,
            )
            print(json.dumps({"status": "cleaned"}))
            return 0

        elif args.command == "status":
            result = await migration_service.status(args.migration_id)
            print(json.dumps(result))
            return 0

        elif args.command == "full-reindex":
            result = await migration_service.full_reindex_from_postgres(
                args.collection,
            )
            print(json.dumps(result))
            return 0

        else:
            logger.error("Unknown command", command=args.command)
            return 1

    except Exception as e:
        logger.error("Command failed", error=str(e), command=args.command)
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    finally:
        session.close()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
