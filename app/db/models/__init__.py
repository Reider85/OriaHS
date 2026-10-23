"""ORM model registry.

Importing this module registers every model on ``Base.metadata`` — also used
by Alembic autogenerate (alembic/env.py imports ``Base`` from here).
"""

from app.db.models.ab_experiment import ABExperiment
from app.db.models.base import Base
from app.db.models.document import Document
from app.db.models.embedding_model import EmbeddingModel
from app.db.models.eval_dataset import EvalDataset
from app.db.models.eval_result import EvalResult
from app.db.models.model_migration import ModelMigration
from app.db.models.outbox import OutboxItem
from app.db.models.search_event import SearchEvent
from app.db.models.user import User
from app.db.models.user_context_profile import UserContextProfile

__all__ = [
    "ABExperiment",
    "Base",
    "Document",
    "EmbeddingModel",
    "ModelMigration",
    "OutboxItem",
    "EvalDataset",
    "EvalResult",
    "SearchEvent",
    "User",
    "UserContextProfile",
]
