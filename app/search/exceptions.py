"""Search-subsystem exceptions (P-09, P-10, P-11)."""


class StatementTimeoutError(Exception):
    """Raised when a PG query exceeds ``statement_timeout``."""


class QdrantTimeoutError(Exception):
    """Raised when Qdrant does not respond within the timeout."""


class QdrantUnavailableError(Exception):
    """Raised when Qdrant is unreachable."""
