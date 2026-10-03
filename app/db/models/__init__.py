"""ORM model registry.

Importing this module registers every model on ``Base.metadata`` — also used
by Alembic autogenerate (alembic/env.py imports ``Base`` from here).
"""

from app.db.models.base import Base
from app.db.models.document import Document
from app.db.models.embedding_model import EmbeddingModel
from app.db.models.eval_dataset import EvalDataset
from app.db.models.eval_result import EvalResult
from app.db.models.outbox import OutboxItem

__all__ = ["Base", "Document", "EmbeddingModel", "OutboxItem", "EvalDataset", "EvalResult"]
