"""Reranker exceptions (C-01)."""


class RerankerUnavailableException(Exception):
    """Reranker service is unavailable (model failed to load or device error)."""

    pass


class RerankerTimeoutException(Exception):
    """Reranker inference exceeded timeout (RerankerConfig.timeout_ms)."""

    pass
