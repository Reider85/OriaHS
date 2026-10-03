"""C-10: Push-down filter tests (ARCHITECT §8.2, ROADMAP §4.2.9).

Unit tests for the push-down selectivity estimation and doc_id pre-filtering.
Uses mocked AsyncSession and fakeredis — no Docker required.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.config import PushdownConfig
from app.search.filters import SearchFilters
from app.search.pushdown import (
    _build_pushdown_where,
    _has_selective_filters,
    maybe_pushdown,
)


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def config() -> PushdownConfig:
    return PushdownConfig(selectivity_threshold=0.1, max_candidate_ids=5000)


def _fake_session(explain_rows: list | None = None, doc_id_rows: list | None = None) -> AsyncMock:
    """Session mock returning controlled EXPLAIN and SELECT results."""
    session = AsyncMock()

    explain_result = MagicMock()
    if explain_rows is not None:
        explain_result.fetchone.return_value = explain_rows
    else:
        explain_result.fetchone.return_value = ([{"Plan": {"Rows": 100}}],)

    doc_id_result = MagicMock()
    if doc_id_rows is not None:
        doc_id_result.fetchall.return_value = doc_id_rows
    else:
        doc_id_result.fetchall.return_value = []

    async def _execute(stmt, params=None):
        stmt_str = str(stmt) if hasattr(stmt, "text") else str(stmt)
        if "EXPLAIN" in stmt_str:
            return explain_result
        return doc_id_result

    session.execute = AsyncMock(side_effect=_execute)
    return session


# ------------------------------------------------------------------
# _has_selective_filters
# ------------------------------------------------------------------


class TestHasSelectiveFilters:
    def test_empty_filters(self) -> None:
        assert _has_selective_filters(SearchFilters()) is False

    def test_language_only(self) -> None:
        assert _has_selective_filters(SearchFilters(language=["en"])) is True

    def test_tags_only(self) -> None:
        assert _has_selective_filters(SearchFilters(tags_any=["ml"])) is True

    def test_attributes_category(self) -> None:
        assert _has_selective_filters(SearchFilters(attributes={"category": "ML"})) is True

    def test_attributes_price(self) -> None:
        assert _has_selective_filters(SearchFilters(attributes={"price": {"gte": 10}})) is True

    def test_created_after(self) -> None:
        from datetime import datetime

        assert _has_selective_filters(SearchFilters(created_after=datetime(2026, 1, 1))) is True


# ------------------------------------------------------------------
# _build_pushdown_where
# ------------------------------------------------------------------


class TestBuildPushdownWhere:
    def test_tenant_and_deleted(self) -> None:
        tenant = uuid4()
        clauses, params = _build_pushdown_where(tenant, SearchFilters())
        assert "tenant_id = :tenant_id" in clauses
        assert "deleted_at IS NULL" in clauses
        assert params["tenant_id"] == str(tenant)

    def test_language_filter(self) -> None:
        clauses, params = _build_pushdown_where(uuid4(), SearchFilters(language=["en", "ru"]))
        assert "language = ANY(:languages)" in clauses
        assert params["languages"] == ["en", "ru"]

    def test_tags_filter(self) -> None:
        clauses, params = _build_pushdown_where(uuid4(), SearchFilters(tags_any=["ml"]))
        assert "tags && :tags" in clauses
        assert params["tags"] == ["ml"]

    def test_category_filter(self) -> None:
        clauses, params = _build_pushdown_where(
            uuid4(), SearchFilters(attributes={"category": "ML"})
        )
        assert "attributes->>'category' = :category" in clauses
        assert params["category"] == "ML"

    def test_price_filter_gte_lte(self) -> None:
        clauses, params = _build_pushdown_where(
            uuid4(), SearchFilters(attributes={"price": {"gte": 10, "lte": 100}})
        )
        assert any("price_gte" in c for c in clauses)
        assert any("price_lte" in c for c in clauses)
        assert params["price_gte"] == 10.0
        assert params["price_lte"] == 100.0

    def test_created_after_filter(self) -> None:
        from datetime import datetime

        dt = datetime(2026, 1, 1)
        clauses, params = _build_pushdown_where(uuid4(), SearchFilters(created_after=dt))
        assert "created_at >= :created_after" in clauses
        assert params["created_after"] == dt


# ------------------------------------------------------------------
# maybe_pushdown
# ------------------------------------------------------------------


class TestMaybePushdown:
    @pytest.mark.asyncio
    async def test_disabled_flag(self, redis: fakeredis.aioredis.FakeRedis) -> None:
        session = _fake_session()
        with patch("app.search.pushdown.settings") as mock_settings:
            mock_settings.pushdown = PushdownConfig()
            mock_settings.feature_flags = MagicMock(pushdown_enabled=False)
            decision = await maybe_pushdown(
                session,
                redis,
                uuid4(),
                SearchFilters(),
            )
        assert decision.use_pushdown is False
        assert decision.reason == "disabled"

    @pytest.mark.asyncio
    async def test_no_filters(self, redis: fakeredis.aioredis.FakeRedis) -> None:
        session = _fake_session()
        with patch("app.search.pushdown.settings") as mock_settings:
            mock_settings.pushdown = PushdownConfig()
            mock_settings.feature_flags = MagicMock(pushdown_enabled=True)
            decision = await maybe_pushdown(
                session,
                redis,
                uuid4(),
                SearchFilters(),
            )
        assert decision.use_pushdown is False
        assert decision.reason == "no_filters"

    @pytest.mark.asyncio
    async def test_selective_filter(
        self,
        redis: fakeredis.aioredis.FakeRedis,
        config: PushdownConfig,
    ) -> None:
        """selectivity 0.05 < 0.1 threshold → push-down used."""
        session = _fake_session(
            explain_rows=([{"Plan": {"Rows": 500}}],),
            doc_id_rows=[(uuid4(),) for _ in range(100)],
        )
        with patch("app.search.pushdown.settings") as mock_settings:
            mock_settings.pushdown = config
            mock_settings.feature_flags = MagicMock(pushdown_enabled=True)

            with patch(
                "app.search.pushdown.get_tenant_doc_count",
                new_callable=AsyncMock,
                return_value=10000,
            ):
                decision = await maybe_pushdown(
                    session,
                    redis,
                    uuid4(),
                    SearchFilters(attributes={"category": "ML"}),
                )

        assert decision.use_pushdown is True
        assert len(decision.doc_ids) == 100
        assert decision.selectivity is not None
        assert decision.selectivity < 0.1

    @pytest.mark.asyncio
    async def test_not_selective(
        self,
        redis: fakeredis.aioredis.FakeRedis,
        config: PushdownConfig,
    ) -> None:
        """selectivity 0.3 >= 0.1 → push-down skipped."""
        session = _fake_session(
            explain_rows=([{"Plan": {"Rows": 3000}}],),
        )
        with patch("app.search.pushdown.settings") as mock_settings:
            mock_settings.pushdown = config
            mock_settings.feature_flags = MagicMock(pushdown_enabled=True)

            with patch(
                "app.search.pushdown.get_tenant_doc_count",
                new_callable=AsyncMock,
                return_value=10000,
            ):
                decision = await maybe_pushdown(
                    session,
                    redis,
                    uuid4(),
                    SearchFilters(attributes={"category": "ML"}),
                )

        assert decision.use_pushdown is False
        assert decision.reason == "not_selective"

    @pytest.mark.asyncio
    async def test_empty_result(
        self,
        redis: fakeredis.aioredis.FakeRedis,
        config: PushdownConfig,
    ) -> None:
        """Zero matching rows → push-down with empty doc_ids."""
        session = _fake_session(
            explain_rows=([{"Plan": {"Rows": 0}}],),
            doc_id_rows=[],
        )
        with patch("app.search.pushdown.settings") as mock_settings:
            mock_settings.pushdown = config
            mock_settings.feature_flags = MagicMock(pushdown_enabled=True)

            with patch(
                "app.search.pushdown.get_tenant_doc_count",
                new_callable=AsyncMock,
                return_value=10000,
            ):
                decision = await maybe_pushdown(
                    session,
                    redis,
                    uuid4(),
                    SearchFilters(attributes={"category": "very_rare"}),
                )

        assert decision.use_pushdown is True
        assert decision.doc_ids == []

    @pytest.mark.asyncio
    async def test_too_large(
        self,
        redis: fakeredis.aioredis.FakeRedis,
        config: PushdownConfig,
    ) -> None:
        """More than max_candidate_ids → push-down skipped."""
        session = _fake_session(
            explain_rows=([{"Plan": {"Rows": 500}}],),
            doc_id_rows=[(uuid4(),) for _ in range(5001)],
        )
        with patch("app.search.pushdown.settings") as mock_settings:
            mock_settings.pushdown = config
            mock_settings.feature_flags = MagicMock(pushdown_enabled=True)

            with patch(
                "app.search.pushdown.get_tenant_doc_count",
                new_callable=AsyncMock,
                return_value=10000,
            ):
                decision = await maybe_pushdown(
                    session,
                    redis,
                    uuid4(),
                    SearchFilters(attributes={"category": "ML"}),
                )

        assert decision.use_pushdown is False
        assert decision.reason == "too_large"


# ------------------------------------------------------------------
# vector_search with pushdown_ids
# ------------------------------------------------------------------


class TestVectorSearchPushdown:
    @pytest.mark.asyncio
    async def test_empty_pushdown_ids_returns_empty(self) -> None:
        """Empty pushdown_ids → short-circuit, no Qdrant call."""
        from app.search.vector import vector_search

        session = AsyncMock()
        qdrant = AsyncMock()
        emb_svc = AsyncMock()
        emb_cache = AsyncMock()

        result = await vector_search(
            session,
            qdrant,
            emb_svc,
            emb_cache,
            query="test",
            tenant_id=uuid4(),
            filters=SearchFilters(),
            pushdown_ids=[],
        )
        assert result == []
        qdrant.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_pushdown_ids_added_to_filter(self) -> None:
        """Non-empty pushdown_ids → MatchAny added to Qdrant filter."""
        from app.search.vector import vector_search

        doc_ids = [uuid4(), uuid4()]
        session = AsyncMock()
        qdrant = AsyncMock()
        qdrant.search = AsyncMock(return_value=[])
        emb_svc = AsyncMock()
        emb_cache = AsyncMock()
        emb_cache.get_query_embedding = AsyncMock(return_value=None)
        emb_svc.embed_query = AsyncMock(
            return_value=__import__("numpy").zeros(1024, dtype=__import__("numpy").float32)
        )

        await vector_search(
            session,
            qdrant,
            emb_svc,
            emb_cache,
            query="test",
            tenant_id=uuid4(),
            filters=SearchFilters(),
            pushdown_ids=doc_ids,
        )

        call_args = qdrant.search.call_args
        qfilter = call_args.kwargs.get("query_filter") or call_args[1].get("query_filter")
        must_conditions = qfilter.must
        pushdown_cond = [
            c for c in must_conditions if hasattr(c, "match") and hasattr(c.match, "any")
        ]
        assert len(pushdown_cond) == 1
        assert len(pushdown_cond[0].match.any) == 2
