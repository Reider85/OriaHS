"""P-03: EmbeddingModel registry — seed, partial unique index, cached lookup."""

import pytest
from sqlalchemy import select

from app.db.models import EmbeddingModel
from app.db.queries.embedding_models import (
    clear_default_model_cache,
    get_default_model,
)


def _add_default(session) -> EmbeddingModel:
    model = EmbeddingModel(
        name="bge-m3-v1",
        dimension=1024,
        description="BAAI/bge-m3 multilingual dense embeddings",
        is_default=True,
        is_active=True,
    )
    session.add(model)
    return model


@pytest.mark.slow  # spins a Postgres testcontainer
async def test_seed_is_default_and_active(session) -> None:
    clear_default_model_cache()
    _add_default(session)
    await session.commit()

    models = (await session.scalars(select(EmbeddingModel))).all()
    assert len(models) == 1
    row = models[0]
    assert row.name == "bge-m3-v1"
    assert row.dimension == 1024
    assert row.is_default is True
    assert row.is_active is True


@pytest.mark.slow
async def test_second_active_default_rejected(session) -> None:
    clear_default_model_cache()
    _add_default(session)
    await session.commit()

    session.add(EmbeddingModel(name="evil-v2", dimension=1024, is_default=True, is_active=True))
    with pytest.raises(Exception, match="one_default_idx"):
        await session.commit()


@pytest.mark.slow
async def test_get_default_model_caches(session, monkeypatch) -> None:
    clear_default_model_cache()
    _add_default(session)
    await session.commit()

    model = await get_default_model(session)
    assert model.name == "bge-m3-v1"

    async def no_sql(*args: object, **kwargs: object) -> object:
        raise AssertionError("cached path must not hit the database")

    monkeypatch.setattr(session, "execute", no_sql)
    cached = await get_default_model(session)
    assert cached.name == "bge-m3-v1"
