"""Speculative rerank (C-04, ARCHITECT §6.5, ROADMAP §4.2.4).

Runs cross-encoder reranking in parallel with vector search, using lexical top-10
as speculative input. Saves ~80ms on p99 by overlapping rerank with slower vector
channel.
"""

import asyncio
import logging
from uuid import UUID

from app.config import RerankerConfig
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.schemas import RerankCandidate, RerankResult
from app.reranker.service import RerankerService
from app.search.exceptions import QdrantTimeoutError, QdrantUnavailableError
from app.search.lexical import LexicalHit
from app.search.vector import VectorHit

logger = logging.getLogger(__name__)


class SpeculativeReranker:
    """Speculative reranker for hybrid search (ARCHITECT §6.5).

    Runs reranking in parallel with vector search using lexical top-10 as speculative
    input. Saves ~80ms on p99 by overlapping rerank with slower vector channel.

    States:
    - Normal: lex finishes first → speculative rerank on lex top-10 while vec runs
    - Fallback: vec finishes first → wait for lex, rerank full fusion top-50
    - Degraded: circuit breaker open → skip rerank entirely
    """

    def __init__(
        self,
        reranker_service: RerankerService,
        circuit_breaker: RerankerCircuitBreaker,
        config: RerankerConfig,
    ) -> None:
        self._reranker = reranker_service
        self._circuit_breaker = circuit_breaker
        self._config = config

    async def run(
        self,
        query: str,
        lex_task: asyncio.Task[list[LexicalHit]],
        vec_task: asyncio.Task[list[VectorHit]],
    ) -> tuple[list[VectorHit], list[RerankResult]]:
        """Run speculative rerank pipeline (ARCHITECT §6.5).

        Args:
            query: Search query
            lex_task: Task returning lexical search results
            vec_task: Task returning vector search results

        Returns:
            tuple: (vec_results, rerank_results) where rerank_results may be empty
                   if circuit breaker is open or fallback occurred

        Raises:
            QdrantTimeoutError: If vector search times out
            QdrantUnavailableError: If vector search fails
        """
        lex_hits: list[LexicalHit] = []
        vec_hits: list[VectorHit] = []
        lex_error: Exception | None = None
        vec_error: Exception | None = None

        # Speculative rerank (ARCHITECT 6.5): the cross-encoder is started from a
        # done-callback on the lexical channel, so it runs *while* the slower
        # vector channel is still in flight. Waiting for both channels before
        # reranking would serialise them and lose the whole point of the design.
        spec_task: asyncio.Task[list[RerankResult]] | None = None

        def _start_speculative() -> None:
            nonlocal spec_task

            # Nothing to overlap: the vector channel already finished, so the
            # full fusion rerank runs instead (and is not wasted work).
            if vec_task.done() or self._circuit_breaker.is_open():
                return

            try:
                hits = lex_task.result()
            except Exception:
                return  # lexical failed: handled after the gather

            if not hits:
                return  # no speculative input, and no fusion rerank is wanted

            candidates = self._build_candidates_from_lexical(hits[: self._config.speculative_top_n])
            if not candidates:
                return

            logger.info(
                "Speculative rerank: lexical finished first",
                extra={
                    "speculative_count": len(candidates),
                    "total_lexical": len(hits),
                },
            )
            spec_task = asyncio.ensure_future(self._rerank_candidates(query, candidates))

        if not self._circuit_breaker.is_open():
            lex_task.add_done_callback(lambda _task: _start_speculative())

        # Await both channels to completion (no FIRST_COMPLETED per forbidden patterns)
        await asyncio.gather(lex_task, vec_task, return_exceptions=True)

        # Extract results and errors
        try:
            lex_hits = lex_task.result()
        except Exception as exc:
            lex_error = exc

        try:
            vec_hits = vec_task.result()
        except (QdrantTimeoutError, QdrantUnavailableError) as exc:
            vec_error = exc
        except Exception as exc:
            vec_error = exc

        # Handle vector search failure - return empty rerank results
        if vec_error is not None:
            logger.warning("Vector search failed, skipping rerank", extra={"error": str(vec_error)})
            await self._discard(spec_task)
            return vec_hits, []

        # Handle circuit breaker open - skip rerank entirely
        if self._circuit_breaker.is_open():
            logger.info("Circuit breaker open, skipping speculative rerank")
            await self._discard(spec_task)
            return vec_hits, []

        # Speculative rerank already running (or done) - reuse its result
        if spec_task is not None:
            return vec_hits, await spec_task

        # Lexical channel failed: rerank the full fusion (vector) results
        if lex_error is not None:
            logger.warning(
                "Lexical search failed, reranking vector results only",
                extra={"error": str(lex_error)},
            )
            return await self._fallback_rerank(query, [], vec_hits)

        # Empty lexical results: nothing to speculate on and no fusion rerank wanted
        if not lex_hits:
            logger.info("No lexical results, skipping speculative rerank")
            return vec_hits, []

        # Vector finished first (or too fast to overlap): rerank full fusion top-50
        return await self._fallback_rerank(query, lex_hits, vec_hits)

    @staticmethod
    async def _discard(task: asyncio.Task[list[RerankResult]] | None) -> None:
        """Cancel an in-flight speculative rerank and swallow its cancellation."""
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _rerank_candidates(
        self,
        query: str,
        candidates: list[RerankCandidate],
    ) -> list[RerankResult]:
        """Run the cross-encoder over pre-built candidates."""
        return await self._reranker.rerank(query, candidates)

    async def _fallback_rerank(
        self,
        query: str,
        lex_hits: list[LexicalHit],
        vec_hits: list[VectorHit],
    ) -> tuple[list[VectorHit], list[RerankResult]]:
        """Fallback rerank: rerank full fusion top-50 after both tasks complete."""
        logger.info(
            "Fallback rerank: vector finished first",
            extra={
                "total_lexical": len(lex_hits),
                "total_vector": len(vec_hits),
            },
        )

        # Build rerank candidates from fusion results (lex + vec)
        fusion_candidates = self._build_candidates_from_fusion(lex_hits, vec_hits)
        if not fusion_candidates:
            return vec_hits, []

        # Run rerank on full fusion results
        rerank_results = await self._reranker.rerank(query, fusion_candidates)

        logger.info(
            "Fallback rerank completed",
            extra={
                "rerank_count": len(rerank_results),
                "total_rerank_ms": getattr(rerank_results[0], "_inference_ms", 0)
                if rerank_results
                else 0,
            },
        )

        return vec_hits, rerank_results

    def _build_candidates_from_lexical(self, lex_hits: list[LexicalHit]) -> list[RerankCandidate]:
        """Build rerank candidates from lexical search results."""
        if not lex_hits:
            return []

        candidates = []
        for hit in lex_hits:
            text = f"{hit.title}\n{hit.content_snippet}"
            candidates.append(RerankCandidate(doc_id=hit.doc_id, text=text, score=hit.score))
        return candidates

    def _build_candidates_from_fusion(
        self, lex_hits: list[LexicalHit], vec_hits: list[VectorHit]
    ) -> list[RerankCandidate]:
        """Build rerank candidates from fusion results (lex + vec top-50)."""
        # Deduplicate by doc_id and take top-50. A doc appearing in both channels
        # is reranked once, keeping the lexical score (it is already fused upstream).
        doc_ids: set[UUID] = set()
        candidates: list[RerankCandidate] = []

        def add(hit: LexicalHit | VectorHit) -> None:
            if hit.doc_id in doc_ids:
                return
            doc_ids.add(hit.doc_id)
            candidates.append(
                RerankCandidate(
                    doc_id=hit.doc_id,
                    text=f"{hit.title}\n{hit.content_snippet}",
                    score=hit.score,
                )
            )

        for lex_hit in lex_hits:
            add(lex_hit)
        for vec_hit in vec_hits:
            add(vec_hit)

        # Return top-50 candidates for reranking
        return candidates[:50]
