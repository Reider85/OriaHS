"""PR-10: Adaptive K unit tests (ARCHITECT §6.4, PROD-PROMPTS PR-10).

Covers the pure ``compute_adaptive_k`` rules (both numeric acceptance
criteria from the spec), the Redis-cached ``get_adaptive_k`` helper and the
orchestrator integration (flag on/off, channel k plumbing, push-down scaling,
per-hit debug keys).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import fakeredis.aioredis
import pytest
from prometheus_client import REGISTRY

from app.api.schemas import SearchFilters, SearchRequest
from app.config import AdaptiveKConfig, FeatureFlags, SearchConfig
from app.db.models.tenant_search_stat import TenantSearchStat
from app.search.adaptive_k import (
    DOC_COUNT_CACHE_TTL_SECONDS,
    MAX_K,
    MIN_K,
    AdaptiveKResult,
    classify_bucket,
    compute_adaptive_k,
    get_adaptive_k,
)
from app.search.lexical import LexicalHit
from app.search.orchestrator import SearchOrchestrator
from app.search.pushdown import PushdownDecision
from app.search.vector import VectorHit


def _stats(doc_count: int) -> TenantSearchStat:
    from datetime import UTC, datetime

    return TenantSearchStat(
        tenant_id=uuid4(),
        doc_count=doc_count,
        avg_query_len=0.0,
        total_searches=0,
        last_updated=datetime.now(UTC),
    )


def _make_request(**overrides) -> SearchRequest:
    defaults = {
        "query": "test query",
        "tenant_id": uuid4(),
        "top_k": 20,
        "timeout_ms": 500,
    }
    defaults.update(overrides)
    return SearchRequest(**defaults)


def _lex_hit(doc_id=None, score: float = 1.0) -> LexicalHit:
    return LexicalHit(
        doc_id=doc_id or uuid4(),
        score=score,
        title="title",
        content_snippet="snippet",
    )


def _vec_hit(doc_id=None, score: float = 0.9) -> VectorHit:
    return VectorHit(
        doc_id=doc_id or uuid4(),
        score=score,
        title="title",
        content_snippet="snippet",
    )


class TestComputeAdaptiveK:
    """Pure-function rules from PROD-PROMPTS PR-10 / ARCHITECT 6.4."""

    def test_small_tenant_short_query_acceptance_criterion(self) -> None:
        # AC: doc_count=1000, query="hi" (len=2 < 5) -> K_lexical=50, K_vector=20
        k_lex, k_vec = compute_adaptive_k(_stats(1000), "hi", AdaptiveKConfig())
        assert k_lex == 50
        assert k_vec == 20

    def test_large_tenant_long_query_acceptance_criterion(self) -> None:
        # AC: doc_count=2M, long query (> 20 chars) -> K_lexical=90, K_vector=120
        query = "very long query with many words here"
        assert len(query) > 20
        k_lex, k_vec = compute_adaptive_k(_stats(2_000_000), query, AdaptiveKConfig())
        assert k_lex == 90
        assert k_vec == 120

    def test_medium_tenant_medium_query_uses_defaults(self) -> None:
        k_lex, k_vec = compute_adaptive_k(_stats(50_000), "some query", AdaptiveKConfig())
        assert k_lex == 50
        assert k_vec == 50

    def test_none_stats_falls_back_to_default_base(self) -> None:
        # New tenant: no stats row -> base K = default (50), adaptation still applies
        k_lex, k_vec = compute_adaptive_k(None, "hi", AdaptiveKConfig())
        assert k_lex == 50 + 20
        assert k_vec == 50 - 10

    def test_min_clamp(self) -> None:
        # base=15 (custom k_small), short query -> K_vector = 15-10 = 5 -> clamped to 10
        config = AdaptiveKConfig(k_small=15)
        k_lex, k_vec = compute_adaptive_k(_stats(100), "hi", config)
        assert k_vec == MIN_K
        assert k_lex == 15 + 20

    def test_max_clamp(self) -> None:
        # base=200 (custom k_large), long query -> K_vector = 200+20 = 220 -> clamped to 200
        config = AdaptiveKConfig(k_large=200)
        query = "x" * 50
        k_lex, k_vec = compute_adaptive_k(_stats(5_000_000), query, config)
        assert k_vec == MAX_K
        assert k_lex == 200 - 10

    def test_default_k_comes_from_search_config(self) -> None:
        # "medium" tenant uses SearchConfig.k_lexical as base (spec Goal 3)
        k_lex, k_vec = compute_adaptive_k(
            _stats(50_000), "some query", AdaptiveKConfig(), default_k=60
        )
        assert k_lex == 60
        assert k_vec == 60


class TestClassifyBucket:
    def test_buckets(self) -> None:
        config = AdaptiveKConfig()
        assert classify_bucket(None, config) == "medium"
        assert classify_bucket(1_000, config) == "small"
        assert classify_bucket(50_000, config) == "medium"
        assert classify_bucket(2_000_000, config) == "large"


class TestGetAdaptiveK:
    @pytest.fixture
    def redis(self) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis()

    @pytest.fixture
    def config(self) -> AdaptiveKConfig:
        return AdaptiveKConfig()

    @pytest.fixture
    def search_config(self) -> SearchConfig:
        return SearchConfig()

    async def test_cache_hit_skips_pg_round_trip(
        self, redis, config, search_config
    ) -> None:
        tenant_id = uuid4()
        await redis.set(f"tenant_stats:doc_count:{tenant_id}", 1000)

        session = AsyncMock()
        result = await get_adaptive_k(
            session, redis, tenant_id, "hi", config, search_config
        )

        assert result is not None
        assert result.k_lexical == 50
        assert result.k_vector == 20
        assert result.bucket == "small"
        session.execute.assert_not_called()

    async def test_cache_miss_loads_from_pg_and_caches(
        self, redis, config, search_config
    ) -> None:
        tenant_id = uuid4()
        row = SimpleNamespace(
            tenant_id=tenant_id,
            doc_count=2_000_000,
            avg_query_len=12.0,
            total_searches=100,
            last_updated=None,
        )
        result_mock = MagicMock()
        result_mock.fetchone.return_value = row
        session = AsyncMock()
        session.execute.return_value = result_mock

        query = "very long query with many words here"
        result = await get_adaptive_k(
            session, redis, tenant_id, query, config, search_config
        )

        assert result is not None
        assert result.k_lexical == 90
        assert result.k_vector == 120
        assert result.bucket == "large"
        # doc_count was cached for 1 hour
        cached = await redis.get(f"tenant_stats:doc_count:{tenant_id}")
        assert cached is not None
        ttl = await redis.ttl(f"tenant_stats:doc_count:{tenant_id}")
        assert 0 < ttl <= DOC_COUNT_CACHE_TTL_SECONDS

    async def test_new_tenant_returns_none(
        self, redis, config, search_config
    ) -> None:
        result_mock = MagicMock()
        result_mock.fetchone.return_value = None
        session = AsyncMock()
        session.execute.return_value = result_mock

        result = await get_adaptive_k(
            session, redis, uuid4(), "query", config, search_config
        )
        assert result is None

    async def test_failure_returns_none_instead_of_raising(
        self, redis, config, search_config
    ) -> None:
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("pg down")

        result = await get_adaptive_k(
            session, redis, uuid4(), "query", config, search_config
        )
        assert result is None


class TestOrchestratorIntegration:
    @pytest.fixture
    def config(self) -> SearchConfig:
        return SearchConfig()

    @pytest.fixture
    def orchestrator(self, config) -> SearchOrchestrator:
        return SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=config,
        )

    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_adaptive_k_flag_off_uses_static_k(
        self, mock_lex, mock_vec, config
    ) -> None:
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]

        flags = FeatureFlags(adaptive_k_enabled=False, pushdown_enabled=False)
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=config,
            feature_flags=flags,
        )

        with patch("app.search.orchestrator.get_redis_client") as mock_redis:
            resp = await orchestrator.search(_make_request())

        assert not resp.degraded
        assert mock_lex.call_args.kwargs["k"] == config.k_lexical
        assert mock_vec.call_args.kwargs["k"] == config.k_vector
        mock_redis.assert_not_called()

    @patch("app.search.orchestrator.get_adaptive_k", new_callable=AsyncMock)
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_adaptive_k_passed_to_channels(
        self, mock_lex, mock_vec, mock_adaptive, orchestrator
    ) -> None:
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        mock_adaptive.return_value = AdaptiveKResult(
            k_lexical=80, k_vector=40, bucket="large"
        )

        resp = await orchestrator.search(_make_request())

        assert not resp.degraded
        assert mock_lex.call_args.kwargs["k"] == 80
        assert mock_vec.call_args.kwargs["k"] == 40

        used = REGISTRY.get_sample_value("adaptive_k_used_total", {"bucket": "large"})
        assert used is not None and used >= 1
        assert REGISTRY.get_sample_value("adaptive_k_lexical") == 80
        assert REGISTRY.get_sample_value("adaptive_k_vector") == 40

    @patch("app.search.orchestrator.get_adaptive_k", new_callable=AsyncMock)
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_adaptive_k_debug_keys_when_explain(
        self, mock_lex, mock_vec, mock_adaptive, orchestrator
    ) -> None:
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        mock_adaptive.return_value = AdaptiveKResult(
            k_lexical=50, k_vector=20, bucket="small"
        )

        resp = await orchestrator.search(_make_request(explain=True))

        debug = resp.hits[0].debug
        assert debug is not None
        assert debug["adaptive_k_lexical"] == 50
        assert debug["adaptive_k_vector"] == 20

    @patch("app.search.orchestrator.get_adaptive_k", new_callable=AsyncMock)
    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.lexical_search", new_callable=AsyncMock)
    async def test_adaptive_k_fallback_to_static_on_none(
        self, mock_lex, mock_vec, mock_adaptive, orchestrator, config
    ) -> None:
        d1 = uuid4()
        mock_lex.return_value = [_lex_hit(d1)]
        mock_vec.return_value = [_vec_hit(d1)]
        mock_adaptive.return_value = None  # new tenant / failure

        resp = await orchestrator.search(_make_request())

        assert not resp.degraded
        assert mock_lex.call_args.kwargs["k"] == config.k_lexical
        assert mock_vec.call_args.kwargs["k"] == config.k_vector

    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.maybe_pushdown", new_callable=AsyncMock)
    async def test_pushdown_max_candidate_ids_scales_with_k_lexical(
        self, mock_pushdown, mock_vec, config
    ) -> None:
        mock_pushdown.return_value = PushdownDecision(
            use_pushdown=False, reason="not_selective", selectivity=0.5
        )
        mock_vec.return_value = []

        flags = FeatureFlags(pushdown_enabled=True)
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=config,
            feature_flags=flags,
        )
        req = _make_request(filters=SearchFilters(language=["ru"]))

        await orchestrator._vector_channel(req, k=40, k_lexical=100)

        cfg = mock_pushdown.call_args.kwargs["config"]
        assert cfg is not None
        # default max_candidate_ids=5000 scaled by k_lexical/default (100/50)
        assert cfg.max_candidate_ids == 10_000

    @patch("app.search.orchestrator.vector_search", new_callable=AsyncMock)
    @patch("app.search.orchestrator.maybe_pushdown", new_callable=AsyncMock)
    async def test_pushdown_config_untouched_when_k_lexical_default(
        self, mock_pushdown, mock_vec, config
    ) -> None:
        mock_pushdown.return_value = PushdownDecision(
            use_pushdown=False, reason="not_selective", selectivity=0.5
        )
        mock_vec.return_value = []

        flags = FeatureFlags(pushdown_enabled=True)
        orchestrator = SearchOrchestrator(
            session=AsyncMock(),
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=config,
            feature_flags=flags,
        )
        req = _make_request(filters=SearchFilters(language=["ru"]))

        await orchestrator._vector_channel(req, k=50, k_lexical=50)

        assert mock_pushdown.call_args.kwargs["config"] is None
