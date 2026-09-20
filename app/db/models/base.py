"""DeclarativeBase for all ORM models.

P-00 defined the naming convention in ``app.db.session.naming_meta``; this
class binds it so every model-created constraint/index gets a stable name.
"""

from sqlalchemy.orm import DeclarativeBase

from app.db.session import naming_meta


class Base(DeclarativeBase):
    """Single metadata for all models (shares the naming convention)."""

    metadata = naming_meta
