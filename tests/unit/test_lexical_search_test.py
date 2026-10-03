"""P-09: lexical search channel — ts_rank + pg_trgm + filters (ARCHITECT §6.1).

Runs against a disposable Postgres testcontainer (slow marker): seeds a few
documents and asserts the SQL pre-filtering, tenant isolation, scoring order
and statement-timeout behaviour of ``lexical_search``.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import Document
from app.search.filters import SearchFilters
from app.search.lexical import lexical_search


def _seed_row(
    *,
    tenant_id: uuid.UUID,
    title: str,
    content: str,
    language: str = "en",
    tags: list[str] | None = None,
    attributes: dict | None = None,
    created_at: datetime | None = None,
    content_hash: str | None = None,
) -> Document:
    return Document(
        tenant_id=tenant_id,
        external_ref=str(uuid.uuid4()),
        title=title,
        content=content,
        language=language,
        tags=tags or [],
        attributes=attributes or {},
        embedding_model="bge-m3-v1",
        embedding_rev=1,
        content_hash=content_hash or uuid.uuid4().hex,
        created_at=created_at or datetime.now(UTC),
    )


async def _seed(session: AsyncSession, rows: list[Document]) -> None:
    session.add_all(rows)
    await session.commit()


@pytest.mark.slow
async def test_returns_matching_hits_with_snippet(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    await _seed(
        session,
        [
            _seed_row(
                tenant_id=tenant,
                title="Alpha beta",
                content="alpha beta " * 20,
            )
        ],
    )

    hits = await lexical_search(session, "alpha beta", tenant, SearchFilters())
    assert len(hits) == 1
    hit = hits[0]
    assert hit.source == "lexical"
    assert hit.title == "Alpha beta"
    assert len(hit.content_snippet) <= 200
    assert hit.content_snippet.startswith("alpha beta")
    assert hit.score > 0


@pytest.mark.slow
async def test_respects_k_limit(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    await _seed(
        session,
        [
            _seed_row(tenant_id=tenant, title=f"Doc {i}", content="alpha beta matching words")
            for i in range(5)
        ],
    )

    hits = await lexical_search(session, "alpha beta", tenant, SearchFilters(), k=2)
    assert len(hits) == 2


@pytest.mark.slow
async def test_results_sorted_by_score_desc(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    strong = _seed_row(
        tenant_id=tenant,
        title="Alpha beta",
        content="alpha beta " * 30,
    )
    weak = _seed_row(
        tenant_id=tenant,
        title="Alpha beta",
        content="alpha beta",
    )
    await _seed(session, [strong, weak])

    hits = await lexical_search(session, "alpha beta", tenant, SearchFilters())
    assert len(hits) == 2
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0].doc_id == strong.id


@pytest.mark.slow
async def test_language_filter(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    await _seed(
        session,
        [
            _seed_row(
                tenant_id=tenant, title="Rus", content="привет мир общий текст", language="ru"
            ),
            _seed_row(
                tenant_id=tenant, title="Eng", content="привет hello world text", language="en"
            ),
        ],
    )

    hits = await lexical_search(session, "привет", tenant, SearchFilters(language=["ru"]))
    assert len(hits) == 1
    assert hits[0].title == "Rus"


@pytest.mark.slow
async def test_tags_any_filter(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    news = _seed_row(tenant_id=tenant, title="News", content="breaking news is here", tags=["news"])
    sports = _seed_row(tenant_id=tenant, title="Sports", content="game result", tags=["sports"])
    await _seed(session, [news, sports])

    hits = await lexical_search(session, "news", tenant, SearchFilters(tags_any=["news"]))
    assert [h.doc_id for h in hits] == [news.id]


@pytest.mark.slow
async def test_attributes_category_filter(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    ai = _seed_row(
        tenant_id=tenant,
        title="AI paper",
        content="artificial intelligence research",
        attributes={"category": "AI"},
    )
    finance = _seed_row(
        tenant_id=tenant,
        title="Finance",
        content="artificial intelligence markets",
        attributes={"category": "Finance"},
    )
    await _seed(session, [ai, finance])

    hits = await lexical_search(
        session, "artificial intelligence", tenant, SearchFilters(attributes={"category": "AI"})
    )
    assert [h.doc_id for h in hits] == [ai.id]


@pytest.mark.slow
async def test_attributes_price_range_filter(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    cheap = _seed_row(
        tenant_id=tenant,
        title="Cheap",
        content="laptop on sale",
        attributes={"price": 99},
    )
    mid = _seed_row(
        tenant_id=tenant,
        title="Mid",
        content="laptop standard",
        attributes={"price": 1500},
    )
    expensive = _seed_row(
        tenant_id=tenant,
        title="Premium",
        content="laptop flagship",
        attributes={"price": 9000},
    )
    await _seed(session, [cheap, mid, expensive])

    hits = await lexical_search(
        session,
        "laptop",
        tenant,
        SearchFilters(attributes={"price": {"gte": 100, "lte": 5000}}),
    )
    assert [h.doc_id for h in hits] == [mid.id]


@pytest.mark.slow
async def test_tenant_isolation(session: AsyncSession) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    a = _seed_row(tenant_id=tenant_a, title="Shared", content="alpha beta gamma")
    b = _seed_row(tenant_id=tenant_b, title="Shared", content="alpha beta gamma")
    await _seed(session, [a, b])

    hits_a = await lexical_search(session, "alpha beta gamma", tenant_a, SearchFilters())
    assert [h.doc_id for h in hits_a] == [a.id]

    hits_b = await lexical_search(session, "alpha beta gamma", tenant_b, SearchFilters())
    assert [h.doc_id for h in hits_b] == [b.id]


@pytest.mark.slow
async def test_empty_result_returns_empty_list(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    await _seed(session, [_seed_row(tenant_id=tenant, title="Alpha", content="alpha content here")])

    hits = await lexical_search(session, "zzqqxx nonexistent gibberish", tenant, SearchFilters())
    assert hits == []


@pytest.mark.slow
async def test_created_after_filter(session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    old = _seed_row(
        tenant_id=tenant,
        title="Old",
        content="alpha beta",
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    fresh = _seed_row(
        tenant_id=tenant,
        title="Fresh",
        content="alpha beta",
        created_at=datetime.now(UTC),
    )
    await _seed(session, [old, fresh])

    hits = await lexical_search(
        session,
        "alpha beta",
        tenant,
        SearchFilters(created_after=datetime.now(UTC) - timedelta(days=1)),
    )
    assert [h.doc_id for h in hits] == [fresh.id]


@pytest.mark.slow
async def test_statement_timeout_cancels_blocked_query(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """An ACCESS EXCLUSIVE lock forces the SELECT to block; 100ms budget fires."""
    tenant = uuid.uuid4()
    await _seed(session, [_seed_row(tenant_id=tenant, title="Locked", content="alpha beta")])

    lock_acquired = asyncio.Event()

    async def _hold_lock() -> None:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("LOCK TABLE documents IN ACCESS EXCLUSIVE MODE"))
                lock_acquired.set()
                await asyncio.sleep(5)

    lock_task = asyncio.create_task(_hold_lock())
    await lock_acquired.wait()
    try:
        with pytest.raises(Exception, match="canceling statement"):
            await lexical_search(session, "alpha beta", tenant, SearchFilters())
    finally:
        lock_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await lock_task
