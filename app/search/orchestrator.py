"""Search orchestrator — fan-out, dedup, fusion, rerank, response (P-11, C-05).

The orchestrator runs lexical (P-09) and vector (P-10) channels in
parallel via ``asyncio.gather``, deduplicates by ``doc_id``, applies the
requested fusion strategy (RRF ``§7.1`` or weighted ``§7.2``), optionally
reranks with the cross-encoder (C-01), and assembles the final
``SearchResponse``.

Rerank strategies (C-05, ARCHITECT §6.1, §6.5, §6.6)
----------------------------------------------------
When ``req.rerank`` is set the orchestrator picks one of three paths:

1. **Speculative** — ``SpeculativeReranker.run()`` (C-04) receives the two
   channel *tasks* and starts cross-encoder inference on the lexical top-10
   while the vector channel is still in flight. The overlap is the whole
   point (≈80 ms off p99), so the orchestrator hands over task ownership
   instead of awaiting the channels itself.
2. **Plain** — with ``RerankerConfig.speculative_enabled=False`` the
   orchestrator waits for fusion and then reranks the fusion top-K, routed
   through the circuit breaker so breaker metrics stay accurate.
3. **Skipped** — when the circuit breaker is open, or the global
   ``rerank_enabled`` flag is off, or no reranker was injected. Rerank is
   never called; the response reports ``rerank_degraded=True`` on the
   per-hit ``debug`` so the client can tell "no rerank" from "no vector".

Reranked documents are promoted above the non-reranked tail rather than
replacing it, so a partial rerank pass cannot silently drop recall.

Degradation
-----------
- **Qdrant unavailable / timeout** → ``degraded=True``, results from
  lexical channel only, ``total_vector=0``.
- **PG timeout** → results from vector channel only, ``total_lexical=0``.
- **Deadline exceeded** → ``partial=True``, results from whichever channel
  finished in time.
- **Both channels fail** → the route layer raises ``500``.

All timing uses ``time.monotonic()`` — never ``time.time()``.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchHit, SearchRequest, SearchResponse
from app.config import FeatureFlags, RerankerConfig, SearchConfig
from app.db.redis_client import get_redis_client
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.observability import metrics
from app.reranker.circuit_breaker import CircuitBreakerOpen, RerankerCircuitBreaker
from app.reranker.exceptions import RerankerTimeoutException, RerankerUnavailableException
from app.reranker.schemas import RerankCandidate, RerankResult
from app.reranker.service import RerankerService
from app.search.exceptions import (
    DeadlockError,
    QdrantTimeoutError,
    QdrantUnavailableError,
    StatementTimeoutError,
)
from app.search.facets import compute_facets
from app.search.fusion import rrf_fuse, weighted_fuse
from app.search.lexical import LexicalHit, lexical_search
from app.search.pushdown import maybe_pushdown
from app.search.speculative import SpeculativeReranker
from app.search.vector import VectorHit, vector_search

logger = logging.getLogger(__name__)

# Sentinel returned by the rerank helpers when a pass produced no usable scores.
_NO_RERANK: dict[UUID, float] = {}


@dataclass(slots=True)
class _ChannelOutcome:
    """Result of collecting the two channel tasks (C-05).

    ``partial`` marks a response assembled after the deadline cut the
    pipeline short; ``rerank_scores`` is empty unless a rerank pass ran.
    """

    lex_hits: list[LexicalHit] = field(default_factory=list)
    vec_hits: list[VectorHit] = field(default_factory=list)
    lex_error: BaseException | None = None
    vec_error: BaseException | None = None
    lex_done: bool = False
    vec_done: bool = False
    partial: bool = False
    rerank_scores: dict[UUID, float] = field(default_factory=dict)
    rerank_degraded: bool = False


class SearchOrchestrator:
    """Orchestrate parallel lexical + vector search with fusion and rerank.

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
        Search pipeline configuration (k_lexical, k_vector, k_rerank, rrf_k, …).
    reranker:
        Cross-encoder service (C-01). ``None`` disables rerank entirely;
        ``req.rerank=True`` then degrades instead of failing.
    circuit_breaker:
        Rolling-window breaker (C-03) guarding the reranker. When ``None``
        the plain rerank path calls the service directly.
    speculative_reranker:
        Speculative runner (C-04). When ``None`` the plain rerank path is
        used even if ``RerankerConfig.speculative_enabled`` is set.
    reranker_config:
        Reranker settings — supplies ``speculative_enabled`` and the
        fusion top-K fed to the cross-encoder.
    feature_flags:
        Global kill switches (C-00). ``rerank_enabled=False`` disables
        rerank process-wide without a redeploy.
    """

    def __init__(
        self,
        session: AsyncSession,
        qdrant_client: AsyncQdrantClient,
        embedding_service: EmbeddingService,
        embedding_cache: EmbeddingCache,
        config: SearchConfig,
        reranker: RerankerService | None = None,
        circuit_breaker: RerankerCircuitBreaker | None = None,
        speculative_reranker: SpeculativeReranker | None = None,
        reranker_config: RerankerConfig | None = None,
        feature_flags: FeatureFlags | None = None,
        qdrant_health_check: bool = True,
    ) -> None:
        self._session = session
        self._qdrant = qdrant_client
        self._emb_svc = embedding_service
        self._emb_cache = embedding_cache
        self._config = config
        self._reranker = reranker
        self._breaker = circuit_breaker
        self._speculative = speculative_reranker
        self._reranker_config = reranker_config or RerankerConfig()
        self._flags = feature_flags or FeatureFlags()
        self._qdrant_health_check = qdrant_health_check

    async def search(self, req: SearchRequest) -> SearchResponse:
        """Execute the full search pipeline: channels → fusion → rerank.

        Runs both channels in parallel, fuses, optionally reranks with the
        cross-encoder, and returns a ``SearchResponse``.
        """
        start = time.monotonic()
        deadline = start + req.timeout_ms / 1000.0

        # Check if vector search should be disabled
        vector_disabled = False
        if not self._flags.vector_search_enabled:
            logger.warning("Vector search disabled via feature flag, using lexical only")
            vector_disabled = True
            metrics.search_degraded_total.labels(reason="vector_disabled").inc()

        # Check Qdrant health if enabled
        qdrant_unavailable = False
        if self._qdrant_health_check and self._flags.vector_search_enabled:
            try:
                is_healthy = await asyncio.wait_for(
                    self._qdrant.get_collection_info("documents"),
                    timeout=0.1
                )
                if not is_healthy:
                    logger.warning("Qdrant health check failed, using lexical only")
                    qdrant_unavailable = True
                    metrics.search_degraded_total.labels(reason="qdrant_unavailable").inc()
            except Exception:
                logger.warning("Qdrant health check failed, using lexical only")
                qdrant_unavailable = True
                metrics.search_degraded_total.labels(reason="qdrant_unavailable").inc()

        # Create tasks based on availability
        lex_task = asyncio.ensure_future(self._lexical_channel(req))
        vec_task = None

        if vector_disabled or qdrant_unavailable:
            # Skip vector channel entirely
            logger.info("Vector channel skipped due to feature flag or health check")
        else:
            vec_task = asyncio.ensure_future(self._vector_channel(req))

        outcome = await self._run_pipeline(req, lex_task, vec_task, deadline)

        if not outcome.lex_done and not outcome.vec_done:
            raise TimeoutError("Search timeout: both channels exceeded deadline")

        degraded = self._resolve_degraded(
            outcome,
            vector_disabled=vector_disabled,
            qdrant_unavailable=qdrant_unavailable,
        )

        lex_hits, vec_hits = outcome.lex_hits, outcome.vec_hits
        fused = self._fuse(req, lex_hits, vec_hits)
        rerank_scores = outcome.rerank_scores
        rerank_applied = bool(rerank_scores)

        meta = self._build_meta(lex_hits, vec_hits)
        ranked = self._apply_rerank(fused, rerank_scores, req.top_k)

        hits = self._build_hits(req, ranked, meta, rerank_applied, outcome.rerank_degraded)

        # Compute facets on the top-K results if requested
        facets = {}
        if req.facets and ranked:
            doc_ids = [doc_id for doc_id, _, _, _ in ranked[:req.top_k]]
            try:
                facets = await compute_facets(
                    doc_ids=doc_ids,
                    session=self._session,
                    facet_fields=req.facets,
                    top_n=req.facet_top_n,
                )
            except Exception as exc:
                logger.warning("Facets computation failed, returning empty", extra={"error": str(exc)})

        latency_ms = int((time.monotonic() - start) * 1000)
        metrics.search_latency_ms.labels(
            tenant_id=str(req.tenant_id), fusion=req.fusion
        ).observe(latency_ms)

        return SearchResponse(
            hits=hits,
            facets=facets,
            total_lexical=len(lex_hits),
            total_vector=len(vec_hits),
            latency_ms=latency_ms,
            degraded=degraded,
            partial=outcome.partial,
        )

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def _lexical_channel(self, req: SearchRequest) -> list[LexicalHit]:
        return await lexical_search(
            self._session,
            req.query,
            req.tenant_id,
            req.filters,
            k=self._config.k_lexical,
        )

    async def _vector_channel(self, req: SearchRequest) -> list[VectorHit]:
        pushdown_ids: list[UUID] | None = None

        if self._flags.pushdown_enabled:
            redis = get_redis_client()
            decision = await maybe_pushdown(
                self._session, redis, req.tenant_id, req.filters,
            )
            metrics.pushdown_selectivity.set(
                decision.selectivity if decision.selectivity is not None else 1.0,
            )
            metrics.qdrant_pushdown_rate_total.labels(result=decision.reason).inc()

            if decision.use_pushdown:
                pushdown_ids = decision.doc_ids
                logger.debug(
                    "Push-down applied",
                    extra={
                        "doc_id_count": len(decision.doc_ids),
                        "selectivity": decision.selectivity,
                    },
                )

        return await vector_search(
            self._session,
            self._qdrant,
            self._emb_svc,
            self._emb_cache,
            req.query,
            req.tenant_id,
            req.filters,
            k=self._config.k_vector,
            pushdown_ids=pushdown_ids,
        )

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        req: SearchRequest,
        lex_task: asyncio.Task[list[LexicalHit]],
        vec_task: asyncio.Task[list[VectorHit]] | None,
        deadline: float,
    ) -> _ChannelOutcome:
        """Dispatch to the speculative, plain-rerank, or collect-only path."""
        rerank_requested = req.rerank and self._flags.rerank_enabled and self._reranker is not None

        if not rerank_requested:
            outcome = await self._collect_channels(lex_task, vec_task, deadline)
            if req.rerank:
                # Caller asked for rerank but it is unavailable (no GPU, flag
                # off, or no service injected) — serve fusion-only.
                outcome.rerank_degraded = True
                logger.warning(
                    "Rerank requested but unavailable, serving fusion-only",
                    extra={"rerank_enabled": self._flags.rerank_enabled, "service_injected": self._reranker is not None},
                )
            return outcome

        if self._breaker is not None and self._breaker.is_open():
            # Early return: RerankerService must not be called at all.
            outcome = await self._collect_channels(lex_task, vec_task, deadline)
            outcome.rerank_degraded = True
            logger.warning("Circuit breaker open, skipping rerank", extra={"state": self._breaker.state()})
            return outcome

        if self._reranker_config.speculative_enabled and self._speculative is not None and vec_task is not None:
            return await self._run_speculative(req, lex_task, vec_task, deadline)

        outcome = await self._collect_channels(lex_task, vec_task, deadline)
        if not outcome.lex_done and not outcome.vec_done:
            return outcome

        outcome.rerank_scores = await self._plain_rerank(req, outcome)
        return outcome

    async def _collect_channels(
        self,
        lex_task: asyncio.Task[list[LexicalHit]],
        vec_task: asyncio.Task[list[VectorHit]] | None,
        deadline: float,
    ) -> _ChannelOutcome:
        """Await both channels under the overall deadline, cancelling stragglers.

        This is the P-11 behaviour, unchanged: whichever channel finishes
        first is harvested while the other is still running, so a slow
        channel never blocks a fast one past the deadline.
        """
        outcome = _ChannelOutcome()
        pending: set[asyncio.Task[Any]] = {lex_task}
        if vec_task is not None:
            pending.add(vec_task)

        while pending and time.monotonic() < deadline:
            done, pending = await asyncio.wait(
                pending,
                timeout=max(0.01, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                self._absorb(outcome, task, is_vector=task is vec_task)

        if pending:
            outcome.partial = True
            logger.warning("Search deadline exceeded, cancelling stragglers", extra={"pending": len(pending)})
            metrics.search_partial_total.inc()

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        return outcome

    def _absorb(self, outcome: _ChannelOutcome, task: asyncio.Task[Any], *, is_vector: bool) -> None:
        """Fold one finished channel task into ``outcome``."""
        try:
            result = task.result()
        except (QdrantTimeoutError, QdrantUnavailableError, StatementTimeoutError) as exc:
            if is_vector:
                outcome.vec_error, outcome.vec_done = exc, True
            else:
                outcome.lex_error, outcome.lex_done = exc, True
            return
        except DeadlockError as exc:
            # Deadlock errors from lexical channel are already retried, treat as timeout
            if is_vector:
                outcome.vec_error, outcome.vec_done = exc, True
            else:
                outcome.lex_error, outcome.lex_done = exc, True
            return
        except Exception as exc:
            if is_vector:
                outcome.vec_error, outcome.vec_done = exc, True
            else:
                outcome.lex_error, outcome.lex_done = exc, True
            return

        if is_vector:
            outcome.vec_hits = list(result)
            outcome.vec_done = True
        else:
            outcome.lex_hits = list(result)
            outcome.lex_done = True

    # ------------------------------------------------------------------
    # Rerank paths
    # ------------------------------------------------------------------

    async def _run_speculative(
        self,
        req: SearchRequest,
        lex_task: asyncio.Task[list[LexicalHit]],
        vec_task: asyncio.Task[list[VectorHit]],
        deadline: float,
    ) -> _ChannelOutcome:
        """Speculative rerank (C-04): hand the live tasks to the runner.

        ``SpeculativeReranker.run`` gathers both tasks itself, which is what
        lets the cross-encoder start on the lexical top-10 while the vector
        channel is still in flight. The remaining time budget is enforced
        here with ``wait_for``; on timeout we fall back to whatever the
        channels produced (plain rerank still applies to that fusion set).
        """
        outcome = _ChannelOutcome()
        timeout = max(0.0, deadline - time.monotonic())

        try:
            vec_hits, rerank_results = await asyncio.wait_for(
                self._speculative.run(req.query, lex_task, vec_task),  # type: ignore[union-attr]
                timeout=timeout,
            )
        except TimeoutError:
            outcome.partial = True
            logger.warning("Speculative rerank exceeded deadline, falling back to fusion")
            for task in (lex_task, vec_task):
                task.cancel()
            collected = await self._collect_channels(lex_task, vec_task, deadline)
            collected.partial = True
            if collected.lex_done or collected.vec_done:
                collected.rerank_scores = await self._plain_rerank(req, collected)
            return collected
        except CircuitBreakerOpen:
            outcome.rerank_degraded = True
        except (RerankerTimeoutException, RerankerUnavailableException) as exc:
            outcome.rerank_degraded = True
            logger.warning("Speculative rerank failed, serving fusion-only", extra={"error": str(exc)})
        except Exception as exc:
            # A rerank failure must never take the read path down with it.
            outcome.rerank_degraded = True
            logger.warning("Speculative rerank raised, serving fusion-only", extra={"error": str(exc)})

        # The runner gathers both tasks, so both are settled by now. If it
        # raised before gathering, fall back to collecting them normally
        # rather than reading a result off a still-pending task.
        if not (lex_task.done() and vec_task.done()):
            collected = await self._collect_channels(lex_task, vec_task, deadline)
            collected.rerank_degraded = outcome.rerank_degraded
            return collected

        self._absorb(outcome, lex_task, is_vector=False)
        self._absorb(outcome, vec_task, is_vector=True)
        if not outcome.lex_done and not outcome.vec_done:
            return outcome

        if not outcome.rerank_degraded:
            speculative_scores = {r.doc_id: r.score for r in (rerank_results or [])}
            if speculative_scores:
                # A speculative pass covers only the lexical top-10; rerank the
                # remaining fusion candidates so the tail is not left unordered.
                remaining = self._rerank_candidates(
                    req,
                    outcome.lex_hits,
                    outcome.vec_hits,
                    exclude=speculative_scores,
                )
                if remaining:
                    extra = await self._plain_rerank(req, outcome, candidates=remaining)
                    speculative_scores.update(extra)
                outcome.rerank_scores = speculative_scores
            else:
                outcome.rerank_scores = await self._plain_rerank(req, outcome)

        return outcome

    async def _plain_rerank(
        self,
        req: SearchRequest,
        outcome: _ChannelOutcome,
        candidates: list[RerankCandidate] | None = None,
    ) -> dict[UUID, float]:
        """Rerank the fusion top-K through the circuit breaker (C-01, C-03).

        Returns a ``doc_id → score`` mapping, empty when rerank was skipped
        or failed. Never raises: a broken reranker degrades the response
        instead of turning into a ``500``.
        """
        reranker = self._reranker
        if reranker is None:
            return dict(_NO_RERANK)

        if candidates is None:
            candidates = self._rerank_candidates(req, outcome.lex_hits, outcome.vec_hits)
        if not candidates:
            return dict(_NO_RERANK)

        try:
            if self._breaker is not None:
                result = await self._breaker.call(reranker.rerank, req.query, candidates)
            else:
                result = await reranker.rerank(req.query, candidates)
        except CircuitBreakerOpen:
            logger.warning("Circuit breaker opened mid-request, skipping rerank")
            return dict(_NO_RERANK)
        except (RerankerTimeoutException, RerankerUnavailableException) as exc:
            logger.warning("Rerank failed, serving fusion-only", extra={"error": str(exc)})
            return dict(_NO_RERANK)
        except Exception as exc:
            logger.warning("Rerank raised, serving fusion-only", extra={"error": str(exc)})
            return dict(_NO_RERANK)

        # RerankerCircuitBreaker.call swallows the exception and returns it as
        # the result value, so an error can arrive as a return, not a raise.
        if isinstance(result, BaseException):
            logger.warning("Rerank failed inside circuit breaker", extra={"error": str(result)})
            return dict(_NO_RERANK)
        if not isinstance(result, list):
            return dict(_NO_RERANK)

        scores = {r.doc_id: r.score for r in result if isinstance(r, RerankResult)}
        if not scores:
            logger.warning("Rerank produced no usable scores, serving fusion-only")
        return scores

    def _rerank_candidates(
        self,
        req: SearchRequest,
        lex_hits: list[LexicalHit],
        vec_hits: list[VectorHit],
        exclude: dict[UUID, float] | None = None,
    ) -> list[RerankCandidate]:
        """Build cross-encoder candidates from the fusion top-K (ARCHITECT §7.3).

        Deduped by ``doc_id``, capped at ``SearchConfig.k_rerank``, and text
        is ``title`` + ``"\\n"`` + snippet. ``exclude`` drops documents that
        a speculative pass already scored.
        """
        skip = exclude or {}
        seen: set[UUID] = set()
        candidates: list[RerankCandidate] = []
        limit = self._config.k_rerank

        for hits in (lex_hits, vec_hits):
            for hit in hits:
                doc_id = hit.doc_id
                if doc_id in seen or doc_id in skip:
                    continue
                seen.add(doc_id)
                candidates.append(
                    RerankCandidate(
                        doc_id=doc_id,
                        text=f"{hit.title}\n{hit.content_snippet}",
                        score=float(hit.score),
                    )
                )
                if len(candidates) >= limit:
                    return candidates
        return candidates

    # ------------------------------------------------------------------
    # Fusion and response assembly
    # ------------------------------------------------------------------

    def _fuse(
        self,
        req: SearchRequest,
        lex_hits: list[LexicalHit],
        vec_hits: list[VectorHit],
    ) -> list[tuple[UUID, float]]:
        """Dispatch to the requested fusion strategy (ARCHITECT §7.1, §7.2)."""
        if req.fusion == "weighted" and self._flags.weighted_fusion_enabled:
            # Track weighted fusion usage
            metrics.fusion_strategy_usage_total.labels(strategy="weighted").inc()
            return weighted_fuse(lex_hits, vec_hits, alpha=req.fusion_alpha)
        if req.fusion == "weighted":
            # Track weighted fusion fallback to RRF
            metrics.fusion_strategy_usage_total.labels(strategy="rrf").inc()
            logger.warning("weighted_fusion_enabled=False, falling back to RRF")
        else:
            # Track RRF usage
            metrics.fusion_strategy_usage_total.labels(strategy="rrf").inc()
        return rrf_fuse(lex_hits, vec_hits, k=self._config.rrf_k)

    def _apply_rerank(
        self,
        fused: list[tuple[UUID, float]],
        rerank_scores: dict[UUID, float],
        top_k: int,
    ) -> list[tuple[UUID, float, float, float | None]]:
        """Order the fused set, promoting reranked documents to the front.

        Returns ``(doc_id, final_score, fusion_score, rerank_score)`` tuples.
        Reranked documents lead, ordered by cross-encoder score; the
        non-reranked tail keeps its fusion order. Promoting rather than
        replacing keeps recall intact when only part of the set was reranked.
        """
        if not rerank_scores:
            return [(doc_id, score, score, None) for doc_id, score in fused[:top_k]]

        reranked: list[tuple[UUID, float, float, float | None]] = []
        tail: list[tuple[UUID, float, float, float | None]] = []
        for doc_id, fusion_score in fused:
            rerank_score = rerank_scores.get(doc_id)
            if rerank_score is None:
                tail.append((doc_id, fusion_score, fusion_score, None))
            else:
                reranked.append((doc_id, rerank_score, fusion_score, rerank_score))

        reranked.sort(key=lambda row: -row[1])
        return (reranked + tail)[:top_k]

    def _build_meta(
        self,
        lex_hits: list[LexicalHit],
        vec_hits: list[VectorHit],
    ) -> tuple[dict[UUID, str], dict[UUID, str], dict[UUID, float], dict[UUID, float], dict[UUID, int], dict[UUID, int]]:
        """Index channel results for O(1) per-hit assembly.

        Building the rank/score lookups up front keeps the hit loop linear
        instead of scanning the channels once per result.
        """
        titles: dict[UUID, str] = {}
        snippets: dict[UUID, str] = {}
        lex_scores: dict[UUID, float] = {}
        vec_scores: dict[UUID, float] = {}
        lex_ranks: dict[UUID, int] = {}
        vec_ranks: dict[UUID, int] = {}

        for rank, lex_hit in enumerate(lex_hits, start=1):
            lex_ranks[lex_hit.doc_id] = rank
            lex_scores[lex_hit.doc_id] = lex_hit.score
            titles.setdefault(lex_hit.doc_id, lex_hit.title)
            snippets.setdefault(lex_hit.doc_id, lex_hit.content_snippet)

        for rank, vec_hit in enumerate(vec_hits, start=1):
            vec_ranks[vec_hit.doc_id] = rank
            vec_scores[vec_hit.doc_id] = vec_hit.score
            titles.setdefault(vec_hit.doc_id, vec_hit.title)
            snippets.setdefault(vec_hit.doc_id, vec_hit.content_snippet)

        return titles, snippets, lex_scores, vec_scores, lex_ranks, vec_ranks

    def _build_hits(
        self,
        req: SearchRequest,
        ranked: list[tuple[UUID, float, float, float | None]],
        meta: tuple[dict[UUID, str], dict[UUID, str], dict[UUID, float], dict[UUID, float], dict[UUID, int], dict[UUID, int]],
        rerank_applied: bool,
        rerank_degraded: bool,
    ) -> list[SearchHit]:
        titles, snippets, lex_scores, vec_scores, lex_ranks, vec_ranks = meta
        hits: list[SearchHit] = []

        for doc_id, score, fusion_score, rerank_score in ranked:
            debug_info: dict[str, Any] | None = None
            if req.explain:
                debug_info = {
                    "fusion_strategy": req.fusion,
                    "fusion_alpha": req.fusion_alpha if req.fusion == "weighted" else None,
                    "rrf_score": fusion_score if req.fusion == "rrf" else None,
                    "weighted_score": fusion_score if req.fusion == "weighted" else None,
                    "lexical_rank": lex_ranks.get(doc_id),
                    "vector_rank": vec_ranks.get(doc_id),
                    "lexical_score": lex_scores.get(doc_id),
                    "vector_score": vec_scores.get(doc_id),
                    "rerank_applied": rerank_applied,
                    "rerank_degraded": rerank_degraded,
                    "rerank_score": rerank_score,
                }
            hits.append(
                SearchHit(
                    doc_id=doc_id,
                    score=score,
                    title=titles.get(doc_id, ""),
                    snippet=snippets.get(doc_id, ""),
                    attributes={},
                    debug=debug_info,
                )
            )
        return hits

    def _resolve_degraded(
        self,
        outcome: _ChannelOutcome,
        *,
        vector_disabled: bool = False,
        qdrant_unavailable: bool = False,
    ) -> bool:
        """``degraded`` is True only when a *channel* was lost (C-05, C-09).

        A skipped rerank is reported as ``rerank_degraded`` on the per-hit
        ``debug`` and deliberately does not flip this flag.

        "Lost" covers three cases, all of which already have a
        ``search_degraded_total`` reason label: the vector channel was disabled
        by flag, Qdrant failed its health check, and a channel raised.
        ``partial`` counts too: the client gets an incomplete answer, and the
        reason exists in the metric set (``deadline_exceeded``).
        """
        degraded = False
        if vector_disabled or qdrant_unavailable:
            degraded = True
        if outcome.partial:
            degraded = True
            metrics.search_degraded_total.labels(reason="deadline_exceeded").inc()
        if outcome.vec_error is not None:
            degraded = True
            logger.warning(
                "Vector channel failed, degrading to lexical only",
                extra={"error": str(outcome.vec_error)},
            )
        if outcome.lex_error is not None:
            degraded = True
            logger.warning(
                "Lexical channel failed, degrading to vector only",
                extra={"error": str(outcome.lex_error)},
            )
        return degraded
