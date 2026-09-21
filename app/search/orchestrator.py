"""Search orchestrator — fan-out, dedup, fusion, response (P-11, ARCHITECT §2.3, §6.1).

The orchestrator runs lexical (P-09) and vector (P-10) channels in
parallel via ``asyncio.gather``, deduplicates by ``doc_id``, applies the
requested fusion strategy (RRF on MVP), and assembles the final
``SearchResponse``.

Partial failures are handled gracefully:

- **Qdrant unavailable / timeout** → ``degraded=True``, results from
  lexical channel only, ``total_vector=0``.
- **PG timeout** → results from vector channel only, ``total_lexical=0``.
- **Both fail** → the route layer raises ``500``.
"""

import asyncio
import logging
import time
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchHit, SearchRequest, SearchResponse
from app.config import SearchConfig
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.fusion import rrf_fuse
from app.search.lexical import LexicalHit, lexical_search
from app.search.vector import VectorHit, vector_search

logger = logging.getLogger(__name__)


class SearchOrchestrator:
    """Orchestrate parallel lexical + vector search with fusion.

    Parameters
    ----------
    session:
        Async SQLAlchemy session for lexical channel + title loading.
    qdrant_client:
        Async Qdrant client for the vector channel.
    embedding_service:
        BGE-M3 embedding model wrapper.
    embedding_cache:
        Redis-backed embedding cache (query + content-hash).
    config:
        Search pipeline configuration (k_lexical, k_vector, rrf_k, etc.).
    """

    def __init__(
        self,
        session: AsyncSession,
        qdrant_client: AsyncQdrantClient,
        embedding_service: EmbeddingService,
        embedding_cache: EmbeddingCache,
        config: SearchConfig,
    ) -> None:
        self._session = session
        self._qdrant = qdrant_client
        self._emb_svc = embedding_service
        self._emb_cache = embedding_cache
        self._config = config

    async def search(self, req: SearchRequest) -> SearchResponse:
        """Execute the full search pipeline.

        Runs both channels in parallel, fuses results, and returns a
        ``SearchResponse``. On partial failure the response is
        ``degraded=True``.
        """
        start = time.monotonic()
        deadline = start + req.timeout_ms / 1000.0

        lex_hits: list[LexicalHit] = []
        vec_hits: list[VectorHit] = []
        lex_error: BaseException | None = None
        vec_error: BaseException | None = None

        async def _run_lexical() -> list[LexicalHit]:
            return await lexical_search(
                self._session,
                req.query,
                req.tenant_id,
                req.filters,
                k=self._config.k_lexical,
            )

        async def _run_vector() -> list[VectorHit]:
            return await vector_search(
                self._session,
                self._qdrant,
                self._emb_svc,
                self._emb_cache,
                req.query,
                req.tenant_id,
                req.filters,
                k=self._config.k_vector,
            )

        lex_task = asyncio.ensure_future(_run_lexical())
        vec_task = asyncio.ensure_future(_run_vector())

        lex_done = vec_done = False

        # Wait for both with the overall deadline
        pending: set[asyncio.Task[list[LexicalHit] | list[VectorHit]]] = {
            lex_task,
            vec_task,
        }
        while pending and time.monotonic() < deadline:
            done, pending = await asyncio.wait(
                pending, timeout=max(0.01, deadline - time.monotonic()), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    result = task.result()
                    if task is lex_task:
                        lex_hits = result  # type: ignore[assignment]
                        lex_done = True
                    else:
                        vec_hits = result  # type: ignore[assignment]
                        vec_done = True
                except (QdrantTimeoutError, QdrantUnavailableError) as exc:
                    if task is vec_task:
                        vec_error = exc
                        vec_done = True
                    else:
                        lex_error = exc
                        lex_done = True
                except Exception as exc:
                    if task is vec_task:
                        vec_error = exc
                        vec_done = True
                    else:
                        lex_error = exc
                        lex_done = True

        # Cancel stragglers
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        # If neither channel completed → total failure
        if not lex_done and not vec_done:
            raise TimeoutError("Search timeout: both channels exceeded deadline")

        degraded = False
        if vec_error is not None:
            degraded = True
            logger.warning(
                "Vector channel failed, degrading to lexical only",
                extra={"error": str(vec_error)},
            )
        if lex_error is not None:
            degraded = True
            logger.warning(
                "Lexical channel failed, degrading to vector only",
                extra={"error": str(lex_error)},
            )

        # Fusion
        fused = rrf_fuse(lex_hits, vec_hits, k=self._config.rrf_k)

        # Build response hits
        # Build lookup for metadata (title/snippet/attributes) from either channel
        meta: dict[UUID, tuple[str, str, dict[str, Any]]] = {}
        for lex_hit in lex_hits:
            meta.setdefault(lex_hit.doc_id, (lex_hit.title, lex_hit.content_snippet, {}))
        for vec_hit in vec_hits:
            meta.setdefault(vec_hit.doc_id, (vec_hit.title, vec_hit.content_snippet, {}))

        hits: list[SearchHit] = []
        top_k = min(req.top_k, len(fused))
        for doc_id, score in fused[:top_k]:
            title, snippet, attributes = meta.get(doc_id, ("", "", {}))
            debug_info: dict[str, Any] | None = None
            if req.explain:
                debug_info = {
                    "rrf_score": score,
                    "lexical_rank": next(
                        (i for i, h in enumerate(lex_hits, 1) if h.doc_id == doc_id), None
                    ),
                    "vector_rank": next(
                        (i for i, h in enumerate(vec_hits, 1) if h.doc_id == doc_id), None
                    ),
                    "lexical_score": next(
                        (h.score for h in lex_hits if h.doc_id == doc_id), None
                    ),
                    "vector_score": next(
                        (h.score for h in vec_hits if h.doc_id == doc_id), None
                    ),
                }
            hits.append(
                SearchHit(
                    doc_id=doc_id,
                    score=score,
                    title=title,
                    snippet=snippet,
                    attributes=attributes,
                    debug=debug_info,
                )
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        return SearchResponse(
            hits=hits,
            facets={},
            total_lexical=len(lex_hits),
            total_vector=len(vec_hits),
            latency_ms=latency_ms,
            degraded=degraded,
        )
