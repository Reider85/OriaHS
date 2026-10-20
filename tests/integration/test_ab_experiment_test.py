"""Integration tests for A/B testing experiments (PR-03).

Full workflow against disposable PG (testcontainers, ``engine`` /
``wired_app`` fixtures): experiment lifecycle through the service and the
HTTP API, sticky assignment, daily metrics aggregation, sequential
significance, budget/duplicate guards and the feature-flag kill switch.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.deps import get_app_config
from app.config import ABTestingConfig, AppConfig
from app.db.queries.ab_experiments import ABExperimentQueries
from app.services.ab_testing import ABTestingService


def _session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def _insert_event(
    session,
    *,
    experiment_id: str,
    variant: str,
    clicked: bool = False,
    position: int | None = None,
    latency_ms: int = 100,
) -> None:
    """Insert one raw search/click row into ``search_events``."""
    tenant_id = uuid4()
    user_id = uuid4()
    if clicked:
        await session.execute(
            text(
                """
                INSERT INTO search_events (
                    user_id, tenant_id, query_text, clicked_doc_id, position,
                    experiment_id, variant, latency_ms
                )
                VALUES (
                    :user_id, :tenant_id, 'ab test query', :doc_id, :position,
                    :experiment_id, :variant, NULL
                )
                """
            ),
            {
                "user_id": str(user_id),
                "tenant_id": str(tenant_id),
                "doc_id": str(uuid4()),
                "position": position,
                "experiment_id": experiment_id,
                "variant": variant,
            },
        )
    else:
        await session.execute(
            text(
                """
                INSERT INTO search_events (
                    user_id, tenant_id, query_text, query_lang, top_doc_ids,
                    experiment_id, variant, latency_ms
                )
                VALUES (
                    :user_id, :tenant_id, 'ab test query', 'en', ARRAY[:doc_id]::uuid[],
                    :experiment_id, :variant, :latency_ms
                )
                """
            ),
            {
                "user_id": str(user_id),
                "tenant_id": str(tenant_id),
                "doc_id": str(uuid4()),
                "experiment_id": experiment_id,
                "variant": variant,
                "latency_ms": latency_ms,
            },
        )


async def test_service_lifecycle(engine: AsyncEngine) -> None:
    """Create -> assign -> stop -> verify rows and cache invalidation."""
    factory = _session_factory(engine)
    exp_id = f"lifecycle-{uuid4().hex[:8]}"
    queries = ABExperimentQueries(ABTestingConfig())

    async with factory() as session:
        service = ABTestingService(session, ABTestingConfig())
        await service.create_experiment(
            experiment_id=exp_id,
            description="lifecycle test",
            variants=["A", "B"],
            weights=[0.6, 0.4],
            start_date=date.today(),
        )

        experiment = await service.get_experiment(exp_id)
        assert experiment is not None
        assert experiment.status == "active"
        assert experiment.variants == {"A": {}, "B": {}}
        assert experiment.weights == {"A": 0.6, "B": 0.4}

        # Sticky: same user -> same variant across repeated assignment
        user_id = uuid4()
        first = service.assign_variant(
            user_id, exp_id, list(experiment.variants.keys()), experiment.weights
        )
        second = service.assign_variant(
            user_id, exp_id, list(experiment.variants.keys()), experiment.weights
        )
        assert first == second
        assert first in ("A", "B")

        await service.stop_experiment(exp_id, "A")

        stopped = await queries.get_by_id(session, exp_id)
        assert stopped is not None
        assert stopped.status == "stopped"
        assert stopped.winning_variant == "A"
        assert stopped.end_date is not None

        # Stopped experiments are not served to the orchestrator
        assert await service.get_experiment(exp_id) is None

        with pytest.raises(ValueError, match="not active"):
            await service.stop_experiment(exp_id, "B")


async def test_significance_and_daily_metrics(engine: AsyncEngine) -> None:
    """Clear CTR winner is significant; daily metrics aggregate per variant."""
    factory = _session_factory(engine)
    exp_id = f"significance-{uuid4().hex[:8]}"
    config = ABTestingConfig(min_samples_per_variant=10)

    async with factory() as session:
        service = ABTestingService(session, config)
        await service.create_experiment(
            experiment_id=exp_id,
            description="significance test",
            variants=["A", "B"],
            start_date=date.today(),
        )

        # A: 120 searches, 60 of them clicked (ctr 0.5); B: 120 searches, 6 clicked.
        # A click is a SEPARATE search_events row (clicked_doc_id set), matching
        # the production write path in routes/search.py.
        for i in range(120):
            await _insert_event(
                session,
                experiment_id=exp_id,
                variant="A",
                latency_ms=50 + (i % 10),
            )
            if i % 2 == 0:
                await _insert_event(
                    session, experiment_id=exp_id, variant="A", clicked=True, position=1
                )
        for i in range(120):
            await _insert_event(
                session,
                experiment_id=exp_id,
                variant="B",
                latency_ms=80,
            )
            if i % 20 == 0:
                await _insert_event(
                    session, experiment_id=exp_id, variant="B", clicked=True, position=1
                )

        result = await service.check_significance(exp_id)
        assert result["insufficient_samples"] is False
        assert result["significant"] is True
        assert result["winning_variant"] == "A"
        assert result["samples_per_variant"] == {"A": 120, "B": 120}
        assert result["p_value"] < 0.05

        metrics = await service.compute_daily_metrics(exp_id, date.today())
        assert metrics["A"]["count"] == 120
        assert metrics["A"]["ctr"] == pytest.approx(0.5, abs=0.01)
        assert metrics["B"]["count"] == 120
        assert metrics["B"]["ctr"] == pytest.approx(0.05, abs=0.01)
        assert metrics["A"]["latency_p95"] > 0
        assert metrics["B"]["no_click_rate"] == pytest.approx(0.95, abs=0.01)


async def test_significance_insufficient_samples(engine: AsyncEngine) -> None:
    """Below min_samples_per_variant the check degrades, never raises."""
    factory = _session_factory(engine)
    exp_id = f"insufficient-{uuid4().hex[:8]}"
    config = ABTestingConfig(min_samples_per_variant=100)

    async with factory() as session:
        service = ABTestingService(session, config)
        await service.create_experiment(
            experiment_id=exp_id,
            description="insufficient test",
            variants=["A", "B"],
            start_date=date.today(),
        )
        for _ in range(3):
            await _insert_event(session, experiment_id=exp_id, variant="A")

        result = await service.check_significance(exp_id)
        assert result["insufficient_samples"] is True
        assert result["significant"] is False
        assert result["winning_variant"] is None


async def test_create_budget_and_duplicate(engine: AsyncEngine) -> None:
    """max_parallel_experiments budget and duplicate ids are ValueErrors."""
    factory = _session_factory(engine)
    config = ABTestingConfig(max_parallel_experiments=2)
    first_id = f"budget-0-{uuid4().hex[:6]}"

    async with factory() as session:
        service = ABTestingService(session, config)
        await service.create_experiment(
            experiment_id=first_id,
            description="budget test",
            variants=["A", "B"],
            start_date=date.today(),
        )
        await service.create_experiment(
            experiment_id=f"budget-1-{uuid4().hex[:6]}",
            description="budget test",
            variants=["A", "B"],
            start_date=date.today(),
        )

        with pytest.raises(ValueError, match="Maximum parallel experiments"):
            await service.create_experiment(
                experiment_id=f"budget-over-{uuid4().hex[:6]}",
                description="over budget",
                variants=["A", "B"],
                start_date=date.today(),
            )

        # Duplicate check runs against free budget: raise the cap and retry
        # the very first id -> unique-constraint race mapped to ValueError.
        roomy = ABTestingService(session, ABTestingConfig(max_parallel_experiments=5))
        with pytest.raises(ValueError, match="already exists"):
            await roomy.create_experiment(
                experiment_id=first_id,
                description="duplicate",
                variants=["A", "B"],
                start_date=date.today(),
            )


async def test_api_experiment_endpoints(wired_app) -> None:
    """HTTP flow: create (201) -> duplicate (409) -> metrics -> stop -> 404."""
    _app, client = wired_app
    exp_id = f"api-{uuid4().hex[:8]}"
    payload = {
        "experiment_id": exp_id,
        "description": "api test",
        "variants": ["control", "treatment"],
        "weights": [0.5, 0.5],
        "start_date": date.today().isoformat(),
    }

    created = await client.post("/experiments", json=payload)
    assert created.status_code == 201
    assert created.json() == {"experiment_id": exp_id}

    duplicate = await client.post("/experiments", json=payload)
    assert duplicate.status_code == 409

    invalid = await client.post(
        "/experiments",
        json={**payload, "experiment_id": f"{exp_id}-invalid", "variants": ["only"]},
    )
    assert invalid.status_code == 422

    weight_mismatch = await client.post(
        "/experiments",
        json={**payload, "experiment_id": f"{exp_id}-weights", "weights": [1.0]},
    )
    assert weight_mismatch.status_code == 422

    listed = await client.get("/experiments")
    assert listed.status_code == 200
    assert exp_id in [entry["experiment_id"] for entry in listed.json()]

    metrics = await client.get(f"/experiments/{exp_id}/metrics?date={date.today().isoformat()}")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["metrics"].keys() == {"control", "treatment"}
    assert body["metrics"]["control"]["count"] == 0

    missing = await client.get("/experiments/does-not-exist/metrics")
    assert missing.status_code == 404

    stopped = await client.post(
        f"/experiments/{exp_id}/stop", json={"winning_variant": "treatment"}
    )
    assert stopped.status_code == 200

    stopped_again = await client.post(
        f"/experiments/{exp_id}/stop", json={"winning_variant": "treatment"}
    )
    assert stopped_again.status_code == 404


async def test_api_feature_flag_disabled(wired_app) -> None:
    """Every /experiments route answers 403 when the kill switch is off."""
    app, client = wired_app

    disabled = AppConfig()
    disabled.feature_flags.ab_testing_enabled = False
    app.dependency_overrides[get_app_config] = lambda: disabled

    created = await client.post(
        "/experiments",
        json={
            "experiment_id": f"disabled-{uuid4().hex[:8]}",
            "description": "flag off",
            "variants": ["A", "B"],
            "start_date": date.today().isoformat(),
        },
    )
    assert created.status_code == 403

    listed = await client.get("/experiments")
    assert listed.status_code == 403
