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
from typing import TYPE_CHECKING, Any
from uuid import UUID

import numpy as np
from opentelemetry import trace
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchHit, SearchRequest, SearchResponse
from app.config import (
    AdaptiveKConfig,
    FeatureFlags,
    MMRConfig,
    RerankerConfig,
    SearchConfig,
    get_settings,
)
from app.db.queries.model_migrations import get_active_search_target
from app.db.redis_client import get_redis_client
from app.embedding.cache import EmbeddingCache
from app.embedding.service import EmbeddingService
from app.observability import metrics
from app.observability.tracing import get_tracer, span
from app.reranker.circuit_breaker import CircuitBreakerOpen, RerankerCircuitBreaker
from app.reranker.exceptions import RerankerTimeoutException, RerankerUnavailableException
from app.reranker.schemas import RerankCandidate, RerankResult
from app.reranker.service import RerankerService
from app.search.adaptive_k import AdaptiveKResult, get_adaptive_k
from app.search.diversify import mmr_diversify_detailed
from app.search.exceptions import (
    DeadlockError,
    QdrantTimeoutError,
    QdrantUnavailableError,
    StatementTimeoutError,
)
from app.search.facets import compute_facets
from app.search.fusion import rrf_fuse, weighted_fuse
from app.search.langid_fasttext import get_langid
from app.search.lexical import LexicalHit, lexical_search
from app.search.pushdown import maybe_pushdown
from app.search.speculative import SpeculativeReranker
from app.search.vector import VectorHit, vector_search
from app.services.personalization import PersonalizationService
from app.services.shadow_traffic import ShadowControlError

if TYPE_CHECKING:
    from app.services.ab_testing import ABTestingService
    from app.services.shadow_traffic import ShadowTrafficService

logger = logging.getLogger(__name__)
tracer = get_tracer("search.orchestrator")
settings = get_settings()

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
        personalization_service: PersonalizationService | None = None,
        ab_testing_service: "ABTestingService | None" = None,
        mmr_config: "MMRConfig | None" = None,
        shadow_traffic_service: "ShadowTrafficService | None" = None,
        adaptive_k_config: AdaptiveKConfig | None = None,
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
        self._personalization = personalization_service
        self._ab_testing = ab_testing_service
        self._mmr_config = mmr_config or MMRConfig()
        self._adaptive_k_config = adaptive_k_config or AdaptiveKConfig()
        self._shadow = shadow_traffic_service

        # Initialize shadow traffic service with orchestrator reference
        if self._shadow:
            self._shadow.orchestrator = self

    @span(tracer, "search.request")
    async def search(self, req: SearchRequest, *, _internal: bool = False) -> SearchResponse:
        """Execute the full search pipeline: channels → fusion → rerank.

        Runs both channels in parallel, fuses, optionally reranks with the
        cross-encoder, and returns a ``SearchResponse``.

        Args:
            req: Search request
            _internal: If True, skip shadow traffic and A/B testing (internal calls)
        """
        start = time.monotonic()
        deadline = start + req.timeout_ms / 1000.0

        # Apply A/B testing if experiment is active (ARCHITECT 11.3, PR-03)
        variant = None
        if (
            req.experiment_id
            and req.user_id is not None
            and self._flags.ab_testing_enabled
            and self._ab_testing is not None
        ):
            try:
                experiment = await self._ab_testing.get_experiment(req.experiment_id)
                if experiment is not None:
                    variant = self._ab_testing.assign_variant(
                        req.user_id,
                        req.experiment_id,
                        list(experiment.variants.keys()),
                        experiment.weights,
                    )
                    metrics.ab_assignments_total.labels(
                        experiment_id=req.experiment_id, variant=variant
                    ).inc()

                    # Apply variant-specific parameter overlay (whitelisted
                    # SearchRequest fields only — unknown overlay keys are
                    # ignored, never injected into the request)
                    variant_params = experiment.variants.get(variant) or {}
                    if variant_params:
                        req = req.model_copy(
                            update={
                                "fusion": variant_params.get("fusion", req.fusion),
                                "fusion_alpha": variant_params.get(
                                    "fusion_alpha", req.fusion_alpha
                                ),
                                "rerank": variant_params.get("rerank", req.rerank),
                                "personalize": variant_params.get("personalize", req.personalize),
                                "diversify": variant_params.get("diversify", req.diversify),
                            }
                        )

                    logger.debug(
                        "A/B variant applied",
                        extra={
                            "experiment_id": req.experiment_id,
                            "variant": variant,
                            "user_id": str(req.user_id),
                        },
                    )
            except Exception as e:
                logger.warning(
                    "A/B testing failed, proceeding with default parameters",
                    extra={"error": str(e)},
                )
                variant = None

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
                    self._qdrant.get_collection("documents"), timeout=0.1
                )
                if not is_healthy:
                    logger.warning("Qdrant health check failed, using lexical only")
                    qdrant_unavailable = True
                    metrics.search_degraded_total.labels(reason="qdrant_unavailable").inc()
            except Exception:
                logger.warning("Qdrant health check failed, using lexical only")
                qdrant_unavailable = True
                metrics.search_degraded_total.labels(reason="qdrant_unavailable").inc()

        # Adaptive K per tenant (ARCHITECT 6.4, PR-10). Computed before the
        # channels start so both channels and push-down use the same K.
        # Budget guard: a dead Redis must not consume the search deadline —
        # on timeout we silently fall back to the static K.
        adaptive_k: AdaptiveKResult | None = None
        if self._flags.adaptive_k_enabled:
            try:
                adaptive_k = await asyncio.wait_for(
                    get_adaptive_k(
                        self._session,
                        get_redis_client(),
                        req.tenant_id,
                        req.query,
                        self._adaptive_k_config,
                        self._config,
                    ),
                    timeout=self._adaptive_k_config.load_timeout_ms / 1000.0,
                )
            except TimeoutError:
                adaptive_k = None
            if adaptive_k is not None:
                metrics.adaptive_k_used_total.labels(bucket=adaptive_k.bucket).inc()
                metrics.adaptive_k_lexical.set(adaptive_k.k_lexical)
                metrics.adaptive_k_vector.set(adaptive_k.k_vector)
                logger.debug(
                    "Adaptive K applied",
                    extra={
                        "tenant_id": str(req.tenant_id),
                        "k_lexical": adaptive_k.k_lexical,
                        "k_vector": adaptive_k.k_vector,
                        "bucket": adaptive_k.bucket,
                    },
                )

        k_lexical = adaptive_k.k_lexical if adaptive_k else self._config.k_lexical
        k_vector = adaptive_k.k_vector if adaptive_k else self._config.k_vector

        # Create tasks based on availability
        lex_task = asyncio.ensure_future(self._lexical_channel(req, k=k_lexical))
        vec_task = None

        if vector_disabled or qdrant_unavailable:
            # Skip vector channel entirely
            logger.info("Vector channel skipped due to feature flag or health check")
        else:
            vec_task = asyncio.ensure_future(
                self._vector_channel(req, k=k_vector, k_lexical=k_lexical)
            )

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

        # Document vectors, filled lazily by personalization and/or diversification.
        doc_vectors: dict[UUID, np.ndarray] = {}

        # Apply personalization if enabled and available
        personalized_debug = None
        if (
            req.personalize
            and req.user_id is not None
            and self._flags.personalization_enabled
            and self._personalization is not None
        ):
            with span(tracer, "personalize", attributes={
                "cold_start": lambda: profile_info.vector is None,
                "profile_version": lambda: profile_info.profile_version,
                "context_profile_key": lambda: profile_info.context_key,
            }):
                # Determine context (None if context personalization disabled)
                context = (
                    req.context
                    if self._flags.context_personalization_enabled
                    else None
                )

                # Get profile info (contextual with fallback to default)
                profile_info = await self._personalization.get_profile_info(
                    req.user_id, req.tenant_id, context
                )

                # Build metadata for personalization
                meta = self._build_meta(lex_hits, vec_hits)
                # Re-embed top documents for personalization (title + snippet)
                personalized_docs: list[str] = []
                personalized_ids: list[UUID] = []

                # Use top-K documents for personalization (limit to avoid excessive embedding)
                limit = min(len(fused), req.top_k * 2)
                for doc_id, _ in fused[:limit]:
                    # Build text from metadata (title + snippet)
                    title = meta[0].get(doc_id, "")  # titles from _build_meta
                    snippet = meta[1].get(doc_id, "")  # snippets from _build_meta
                    if title or snippet:
                        text = f"{title}\n{snippet}"
                        personalized_docs.append(text)
                        personalized_ids.append(doc_id)

                if personalized_docs:
                    # Re-embed documents using the embedding service (cache-backed)
                    embedded_vectors = await self._emb_svc.embed_texts(personalized_docs)

                    # Map embeddings back to doc_ids
                    for i, doc_id in enumerate(personalized_ids):
                        if i < len(embedded_vectors):
                            doc_vectors[doc_id] = embedded_vectors[i]

                    # Apply personalization scoring (with optional profile override)
                    fused = await self._personalization.personalize_scores(
                        fused, req.user_id, req.tenant_id, doc_vectors, profile_vector=profile_info.vector
                    )

                    # Prepare debug info
                    personalized_debug = {
                        "applied": True,
                        "cold_start": profile_info.vector is None,
                        "profile_version": profile_info.profile_version,
                        "context_profile_key": profile_info.context_key,
                    }
                    logger.debug(
                        "Personalization applied",
                        extra={
                            "user_id": str(req.user_id),
                            "cold_start": profile_info.vector is None,
                            "context_key": profile_info.context_key,
                        },
                    )

        rerank_scores = outcome.rerank_scores
        rerank_applied = bool(rerank_scores)

        meta = self._build_meta(lex_hits, vec_hits)
        ranked = self._apply_rerank(fused, rerank_scores, req.top_k)

        # Apply MMR diversification if enabled (ARCHITECT §10.5, PR-04).
        diversified_debug = None
        if (
            req.diversify
            and self._flags.diversify_enabled
            and len(ranked) > 1  # No diversify needed for 0 or 1 docs
        ):
            metrics.mmr_diversify_total.labels(
                adaptive=str(self._mmr_config.adaptive_enabled)
            ).inc()

            # Embed any document that is still missing a vector (diversify-only path).
            missing_ids = [
                doc_id for doc_id, _, _, _ in ranked if doc_id not in doc_vectors
            ]
            if missing_ids:
                limit = min(len(missing_ids), req.top_k * 2)
                texts_to_embed: list[str] = []
                embedded_ids: list[UUID] = []
                for doc_id in missing_ids[:limit]:
                    title = meta[0].get(doc_id, "")
                    snippet = meta[1].get(doc_id, "")
                    if title or snippet:
                        texts_to_embed.append(f"{title}\n{snippet}")
                        embedded_ids.append(doc_id)
                if texts_to_embed:
                    vectors = await self._emb_svc.embed_texts(texts_to_embed)
                    for i, doc_id in enumerate(embedded_ids):
                        if i < len(vectors):
                            doc_vectors[doc_id] = vectors[i]

            # Resolve lambda parameter (request override wins over config default).
            lambda_param = (
                req.diversify_lambda
                if req.diversify_lambda is not None
                else self._mmr_config.default_lambda
            )

            # Apply MMR diversification
            mmr_result = mmr_diversify_detailed(
                candidates=[(doc_id, score) for doc_id, score, _, _ in ranked],
                doc_vectors=doc_vectors,
                lambda_param=lambda_param,
                top_k=req.top_k,
                lambda_adaptive=self._mmr_config.adaptive_enabled,
                diversity_threshold=self._mmr_config.diversity_threshold,
                lambda_step=self._mmr_config.lambda_step,
                min_lambda=self._mmr_config.min_lambda,
            )

            # Rebuild ranked in MMR order, preserving fusion/rerank debug info
            rank_by_id = {item[0]: item for item in ranked}
            ranked = [
                rank_by_id.get(doc_id, (doc_id, score, score, None))
                for doc_id, score in mmr_result.doc_ids
            ]

            # Update metrics and debug info
            metrics.mmr_diversity_score.set(mmr_result.diversity_score or 0.0)
            metrics.mmr_lambda_param.set(mmr_result.lambda_used)

            # Prepare debug info for hits
            if req.explain:
                diversified_debug = {
                    "diversified": True,
                    "lambda_param": mmr_result.lambda_used,
                    "diversity_score": mmr_result.diversity_score,
                    "adaptive": mmr_result.adaptive,
                }
                logger.debug(
                    "MMR diversification applied",
                    extra={
                        "lambda_used": mmr_result.lambda_used,
                        "diversity_score": mmr_result.diversity_score,
                        "adaptive": mmr_result.adaptive,
                    },
                )
        elif req.explain:
            # Diversification was requested but not applied (flag off or a
            # single candidate) — report it explicitly for the client.
            diversified_debug = {
                "diversified": False,
                "lambda_param": None,
                "diversity_score": None,
                "adaptive": False,
            }

        adaptive_k_debug: dict[str, Any] | None = None
        if adaptive_k is not None and req.explain:
            adaptive_k_debug = {
                "adaptive_k_lexical": adaptive_k.k_lexical,
                "adaptive_k_vector": adaptive_k.k_vector,
            }

        hits = self._build_hits(
            req, ranked, meta, rerank_applied, outcome.rerank_degraded,
            diversified_debug, adaptive_k_debug,
        )

        # Compute facets on the top-K results if requested
        facets = {}
        if req.facets and ranked:
            with span(tracer, "pg.facets", attributes={
                "n_fields": lambda: len(req.facets),
                "top_n": lambda: req.facet_top_n,
            }):
                doc_ids = [doc_id for doc_id, _, _, _ in ranked[: req.top_k]]
                try:
                    facets = await compute_facets(
                        doc_ids=doc_ids,
                        session=self._session,
                        facet_fields=req.facets,
                        top_n=req.facet_top_n,
                    )
                except Exception as exc:
                    logger.warning(
                        "Facets computation failed, returning empty", extra={"error": str(exc)}
                    )

        latency_ms = int((time.monotonic() - start) * 1000)

        # Update span attributes with final metrics
        current_span = trace.get_current_span()
        if current_span:
            current_span.set_attribute("latency_ms", latency_ms)
            current_span.set_attribute("degraded", degraded)

        metrics.search_latency_ms.labels(tenant_id=str(req.tenant_id), fusion=req.fusion).observe(
            latency_ms
        )

        # Expose personalization debug info if applied
        if personalized_debug and req.explain:
            # Add per-hit personalization info when explain=True
            for hit in hits:
                if hit.debug is None:
                    hit.debug = {}
                hit.debug["personalized"] = personalized_debug["applied"]
                hit.debug["cold_start"] = personalized_debug["cold_start"]
                hit.debug["profile_version"] = personalized_debug["profile_version"]

        response = SearchResponse(
            hits=hits,
            facets=facets,
            total_lexical=len(lex_hits),
            total_vector=len(vec_hits),
            latency_ms=latency_ms,
            degraded=degraded,
            partial=outcome.partial,
            personalization=personalized_debug,
            variant=variant,
        )

        # Shadow traffic integration (PR-08)
        # Only run for external requests (not internal shadow calls)
        shadow_experiment_id = None
        if not _internal and self._shadow is not None and self._flags.shadow_traffic_enabled:
            shadow_experiment_id = self._shadow.should_sample()
            # Skip shadow if request already has an experiment_id (avoid attribution conflict)
            if shadow_experiment_id and req.experiment_id is not None:
                shadow_experiment_id = None

        # Fire-and-forget logging of search events (uses fresh session to avoid race with request cleanup)
        async def log_search_event() -> None:
            try:
                from app.db.queries.search_events import search_event_queries
                from app.db.session import async_session_factory

                # Use fresh session for background logging
                async with async_session_factory() as bg_session:
                    # Extract top-K doc_ids from hits for logging
                    top_doc_ids = [hit.doc_id for hit in hits[: req.top_k]]

                    # Detect query language
                    query_lang = None
                    try:
                        lang, _ = await get_langid().detect(req.query)
                        query_lang = lang
                    except Exception:
                        # Language detection failed, use None
                        pass

                    await search_event_queries.insert_search_event(
                        session=bg_session,
                        user_id=str(req.user_id) if req.user_id else None,
                        tenant_id=str(req.tenant_id),
                        query_text=req.query,
                        query_lang=query_lang,
                        top_doc_ids=[str(doc_id) for doc_id in top_doc_ids],
                        fusion_strategy=req.fusion,
                        experiment_id=req.experiment_id,
                        variant=variant,
                        latency_ms=latency_ms,
                    )

                    # PR-10: running average query length + search counter
                    # (atomic upsert, same fire-and-forget path as search_events)
                    if self._flags.adaptive_k_enabled:
                        from app.db.queries import tenant_stats as tenant_stats_queries

                        await tenant_stats_queries.update_avg_query_len(
                            bg_session, req.tenant_id, len(req.query)
                        )

                    logger.debug("Search event logged", extra={"tenant_id": str(req.tenant_id)})

            except Exception as e:
                # Never let logging failures affect the response
                logger.warning("Failed to log search event", extra={"error": str(e)})

        # Start background logging or shadow task
        if shadow_experiment_id:
            # Shadow traffic: suppress main logging, start shadow background task
            asyncio.create_task(self._shadow_background(req, response, shadow_experiment_id))
        else:
            # Normal logging: fire-and-forget search event
            asyncio.create_task(log_search_event())

        return response

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    @span(tracer, "pg.lexical")
    async def _lexical_channel(self, req: SearchRequest, *, k: int) -> list[LexicalHit]:
        # The decorator lambdas are evaluated at span creation, before the
        # adaptive K is known — set the effective K explicitly (PR-10).
        current_span = trace.get_current_span()
        if current_span is not None:
            current_span.set_attribute("k", k)
        return await lexical_search(
            self._session,
            req.query,
            req.tenant_id,
            req.filters,
            k=k,
        )

    @span(tracer, "qdrant.vector")
    async def _vector_channel(
        self, req: SearchRequest, *, k: int, k_lexical: int
    ) -> list[VectorHit]:
        # Decorator lambdas run before adaptive K is known — set K explicitly (PR-10).
        current_span = trace.get_current_span()
        if current_span is not None:
            current_span.set_attribute("k", k)
        pushdown_ids: list[UUID] | None = None

        if self._flags.pushdown_enabled:
            redis = get_redis_client()
            # PR-10: max_candidate_ids scales proportionally with K_lexical.
            pushdown_config = None
            if k_lexical != self._config.k_lexical:
                ratio = k_lexical / self._config.k_lexical
                pushdown_config = settings.pushdown.model_copy(
                    update={
                        "max_candidate_ids": max(
                            k_lexical,
                            int(settings.pushdown.max_candidate_ids * ratio),
                        )
                    }
                )
            decision = await maybe_pushdown(
                self._session,
                redis,
                req.tenant_id,
                req.filters,
                config=pushdown_config,
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

        # Get active search target (collection and model name)
        try:
            collection_name, model_name = get_active_search_target(self._session)
        except Exception:
            # Fallback to default settings if there's any error
            collection_name = settings.qdrant.collection
            model_name = settings.embedding.model_name
            logger.warning(
                "Failed to get active search target, using defaults",
                extra={
                    "collection": collection_name,
                    "model": model_name,
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
            collection_name=collection_name,
            model_name=model_name,
            k=k,
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
            outcome = await self._collect_channels(lex_task, vec_task, deadline, req.tenant_id)
            if req.rerank:
                # Caller asked for rerank but it is unavailable (no GPU, flag
                # off, or no service injected) — serve fusion-only.
                outcome.rerank_degraded = True
                logger.warning(
                    "Rerank requested but unavailable, serving fusion-only",
                    extra={
                        "rerank_enabled": self._flags.rerank_enabled,
                        "service_injected": self._reranker is not None,
                    },
                )
            return outcome

        if self._breaker is not None and self._breaker.is_open():
            # Early return: RerankerService must not be called at all.
            outcome = await self._collect_channels(lex_task, vec_task, deadline, req.tenant_id)
            outcome.rerank_degraded = True
            logger.warning(
                "Circuit breaker open, skipping rerank", extra={"state": self._breaker.state()}
            )
            return outcome

        if (
            self._reranker_config.speculative_enabled
            and self._speculative is not None
            and vec_task is not None
        ):
            return await self._run_speculative(req, lex_task, vec_task, deadline)

        outcome = await self._collect_channels(lex_task, vec_task, deadline, req.tenant_id)
        if not outcome.lex_done and not outcome.vec_done:
            return outcome

        outcome.rerank_scores = await self._plain_rerank(req, outcome)
        return outcome

    async def _collect_channels(
        self,
        lex_task: asyncio.Task[list[LexicalHit]],
        vec_task: asyncio.Task[list[VectorHit]] | None,
        deadline: float,
        tenant_id: UUID,  # type: ignore
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
            logger.warning(
                "Search deadline exceeded, cancelling stragglers", extra={"pending": len(pending)}
            )
            metrics.search_partial_total.labels(tenant_id=str(tenant_id)).inc()

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        return outcome

    def _absorb(
        self, outcome: _ChannelOutcome, task: asyncio.Task[Any], *, is_vector: bool
    ) -> None:
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
            collected = await self._collect_channels(lex_task, vec_task, deadline, req.tenant_id)
            collected.partial = True
            if collected.lex_done or collected.vec_done:
                collected.rerank_scores = await self._plain_rerank(req, collected)
            return collected
        except CircuitBreakerOpen:
            outcome.rerank_degraded = True
        except (RerankerTimeoutException, RerankerUnavailableException) as exc:
            outcome.rerank_degraded = True
            logger.warning(
                "Speculative rerank failed, serving fusion-only", extra={"error": str(exc)}
            )
        except Exception as exc:
            # A rerank failure must never take the read path down with it.
            outcome.rerank_degraded = True
            logger.warning(
                "Speculative rerank raised, serving fusion-only", extra={"error": str(exc)}
            )

        # The runner gathers both tasks, so both are settled by now. If it
        # raised before gathering, fall back to collecting them normally
        # rather than reading a result off a still-pending task.
        if not (lex_task.done() and vec_task.done()):
            collected = await self._collect_channels(lex_task, vec_task, deadline, req.tenant_id)
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

    @span(tracer, "fusion")
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
    ) -> tuple[
        dict[UUID, str],
        dict[UUID, str],
        dict[UUID, float],
        dict[UUID, float],
        dict[UUID, int],
        dict[UUID, int],
    ]:
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
        meta: tuple[
            dict[UUID, str],
            dict[UUID, str],
            dict[UUID, float],
            dict[UUID, float],
            dict[UUID, int],
            dict[UUID, int],
        ],
        rerank_applied: bool,
        rerank_degraded: bool,
        diversified_debug: dict[str, Any] | None = None,
        adaptive_k_debug: dict[str, Any] | None = None,
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
                # Add diversification debug if applied
                if diversified_debug:
                    debug_info.update(diversified_debug)
                # Add adaptive K debug if applied (PR-10)
                if adaptive_k_debug:
                    debug_info.update(adaptive_k_debug)
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

    def fork(self, session: AsyncSession) -> "SearchOrchestrator":
        """Create a new SearchOrchestrator with the same components but fresh session.

        Required for concurrent shadow traffic execution (asyncpg sessions are not concurrency-safe).
        """
        from app.config import get_settings

        get_settings()  # type: ignore
        return SearchOrchestrator(
            session=session,
            qdrant_client=self._qdrant,
            embedding_service=self._emb_svc,
            embedding_cache=self._emb_cache,
            config=self._config,
            reranker=self._reranker,
            circuit_breaker=self._breaker,
            speculative_reranker=self._speculative,
            reranker_config=self._reranker_config,
            feature_flags=self._flags,
            qdrant_health_check=self._qdrant_health_check,
            personalization_service=self._personalization,
            ab_testing_service=self._ab_testing,
            mmr_config=self._mmr_config,
            shadow_traffic_service=self._shadow,
            adaptive_k_config=self._adaptive_k_config,
        )

    async def _shadow_background(
        self,
        req: SearchRequest,
        response: SearchResponse,
        experiment_id: str
    ) -> None:
        """Background task for shadow traffic execution and logging.

        Args:
            req: Original search request
            response: Main search response (control result fallback)
            experiment_id: Shadow experiment ID
        """
        if self._shadow is None:
            return

        try:
            # Execute shadow traffic with fresh orchestrator instances
            shadow_result = await self._shadow.run_shadow(req, self)

            # Log shadow results
            await self._shadow.log_shadow(
                shadow_result=shadow_result,
                user_id=str(req.user_id) if req.user_id else None,
                tenant_id=str(req.tenant_id),
                experiment_id=experiment_id,
            )

        except Exception as e:
            # Fallback: log the main response as control if shadow control failed
            if isinstance(e, ShadowControlError):
                logger.warning(
                    "Shadow control search failed, falling back to main response",
                    extra={"error": str(e)},
                )
                try:
                    from app.config import get_settings
                    from app.db.queries.search_events import SearchEventQueries

                    settings = get_settings()
                    queries = SearchEventQueries(settings.search_events)

                    async with queries.get_session() as session:
                        # Detect query language
                        query_lang = None
                        try:
                            lang, _ = await get_langid().detect(req.query)
                            query_lang = lang
                        except Exception:
                            # Language detection failed, use None
                            pass

                        await queries.insert_search_event(
                            session,
                            user_id=str(req.user_id) if req.user_id else None,
                            tenant_id=str(req.tenant_id),
                            query_text=req.query,
                            query_lang=query_lang,
                            top_doc_ids=response.hits,
                            fusion_strategy=response.fusion_strategy,
                            experiment_id=experiment_id,
                            variant="control",
                            latency_ms=response.latency_ms,
                        )
                except Exception as log_error:
                    logger.error(
                        "Failed to fallback shadow logging",
                        extra={"error": str(log_error)},
                    )

            # Record error metric for any shadow failure
            metrics.shadow_traffic_errors_total.labels(
                reason="shadow_background_failed",
                experiment_id=experiment_id,
            ).inc()
