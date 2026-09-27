"""Speculative rerank (C-04, ARCHITECT §6.5, ROADMAP §4.2.4).

Runs cross-encoder reranking in parallel with vector search, using lexical top-10
as speculative input. Saves ~80ms on p99 by overlapping rerank with slower vector
channel.
"""

import asyncio
import logging
import time
from typing import Any, Literal

from app.config import RerankerConfig
from app.reranker.circuit_breaker import RerankerCircuitBreaker
from app.reranker.exceptions import RerankerUnavailableException
from app.reranker.service import RerankerService
from app.reranker.schemas import RerankCandidate, RerankResult
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

    def __init__(self, reranker_service: RerankerService, circuit_breaker: RerankerCircuitBreaker, config: RerankerConfig) -> None:
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
        # Get completion timestamps to determine which finished first
        lex_done_time: float | None = None
        vec_done_time: float | None = None
        lex_hits: list[LexicalHit] = []
        vec_hits: list[VectorHit] = []
        lex_error: Exception | None = None
        vec_error: Exception | None = None

        # Await both tasks to completion (no FIRST_COMPLETED per forbidden patterns)
        await asyncio.gather(lex_task, vec_task, return_exceptions=True)

        # Extract results and errors
        try:
            lex_hits = lex_task.result()
            lex_done_time = time.time()
        except Exception as exc:
            lex_error = exc
            lex_done_time = time.time()

        try:
            vec_hits = vec_task.result()
            vec_done_time = time.time()
        except (QdrantTimeoutError, QdrantUnavailableError) as exc:
            vec_error = exc
            vec_done_time = time.time()
        except Exception as exc:
            vec_error = exc
            vec_done_time = time.time()

        # Handle vector search failure - return empty rerank results
        if vec_error is not None:
            logger.warning("Vector search failed, skipping rerank", extra={"error": str(vec_error)})
            return vec_hits, []

        # Handle circuit breaker open - skip rerank entirely
        if self._circuit_breaker.is_open():
            logger.info("Circuit breaker open, skipping speculative rerank")
            return vec_hits, []

        # Determine which task finished first to choose strategy
        if lex_done_time is not None and vec_done_time is not None:
            if lex_done_time <= vec_done_time:
                # Lexical finished first (normal path)
                return await self._speculative_rerank(query, lex_hits, vec_hits)
            else:
                # Vector finished first (fallback path)
                return await self._fallback_rerank(query, lex_hits, vec_hits)
        else:
            # One task failed, fallback to full rerank
            return await self._fallback_rerank(query, lex_hits, vec_hits)

    async def _speculative_rerank(
        self,
        query: str,
        lex_hits: list[LexicalHit],
        vec_hits: list[VectorHit],
    ) -> tuple[list[VectorHit], list[RerankResult]]:
        """Speculative rerank: rerank lex top-N while vector is still running."""
        logger.info("Speculative rerank: lexical finished first", extra={
            "speculative_count": min(len(lex_hits), self._config.speculative_top_n),
            "total_lexical": len(lex_hits),
            "total_vector": len(vec_hits),
        })

        # Build rerank candidates from lexical top-N
        candidates = self._build_candidates_from_lexical(lex_hits[:self._config.speculative_top_n])
        if not candidates:
            return vec_hits, []

        # Run rerank speculatively
        rerank_results = await self._reranker.rerank(query, candidates)
        rerank_scores = {r.doc_id: r.score for r in rerank_results}

        logger.info("Speculative rerank completed", extra={
            "speculative_count": len(rerank_results),
            "remaining_count": len(vec_hits),  # Will be updated in orchestrator
            "total_rerank_ms": getattr(rerank_results[0], '_inference_ms', 0) if rerank_results else 0,
        })

        return vec_hits, rerank_results

    async def _fallback_rerank(
        self,
        query: str,
        lex_hits: list[LexicalHit],
        vec_hits: list[VectorHit],
    ) -> tuple[list[VectorHit], list[RerankResult]]:
        """Fallback rerank: rerank full fusion top-50 after both tasks complete."""
        logger.info("Fallback rerank: vector finished first", extra={
            "total_lexical": len(lex_hits),
            "total_vector": len(vec_hits),
        })

        # Build rerank candidates from fusion results (lex + vec)
        fusion_candidates = self._build_candidates_from_fusion(lex_hits, vec_hits)
        if not fusion_candidates:
            return vec_hits, []

        # Run rerank on full fusion results
        rerank_results = await self._reranker.rerank(query, fusion_candidates)
        
        logger.info("Fallback rerank completed", extra={
            "rerank_count": len(rerank_results),
            "total_rerank_ms": getattr(rerank_results[0], '_inference_ms', 0) if rerank_results else 0,
        })

        return vec_hits, rerank_results

    def _build_candidates_from_lexical(self, lex_hits: list[LexicalHit]) -> list[RerankCandidate]:
        """Build rerank candidates from lexical search results."""
        if not lex_hits:
            return []
        
        candidates = []
        for hit in lex_hits:
            text = f"{hit.title}\n{hit.content_snippet}"
            candidates.append(RerankCandidate(
                doc_id=hit.doc_id,
                text=text,
                score=hit.score
            ))
        return candidates

    def _build_candidates_from_fusion(self, lex_hits: list[LexicalHit], vec_hits: list[VectorHit]) -> list[RerankCandidate]:
        """Build rerank candidates from fusion results (lex + vec top-50)."""
        # Deduplicate by doc_id and take top-50
        doc_ids = set()
        candidates = []
        
        # Add lexical hits
        for hit in lex_hits:
            if hit.doc_id not in doc_ids:
                doc_ids.add(hit.doc_id)
                text = f"{hit.title}\n{hit.content_snippet}"
                candidates.append(RerankCandidate(
                    doc_id=hit.doc_id,
                    text=text,
                    score=hit.score
                ))
        
        # Add vector hits (avoid duplicates)
        for hit in vec_hits:
            if hit.doc_id not in doc_ids:
                doc_ids.add(hit.doc_id)
                text = f"{hit.title}\n{hit.content_snippet}"
                candidates.append(RerankCandidate(
                    doc_id=hit.doc_id,
                    text=text,
                    score=hit.score
                ))
        
        # Return top-50 candidates for reranking
        return candidates[:50]