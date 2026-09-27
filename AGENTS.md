# AGENTS.md — OriaHS hybrid search backend

## Quick commands

```bash
uv sync                    # install deps into .venv
uv run uvicorn app.main:app --reload --port 8000   # dev server

uv run ruff check app/ tests/   # lint
uv run ruff format app/ tests/  # format
uv run mypy app/                # typecheck (strict, pydantic plugin)
uv run pytest                   # all tests
uv run pytest tests/unit/       # unit only (no infra needed)
uv run pytest -m "not slow"     # skip slow/docker tests
```

Order matters: **lint → typecheck → test**.

## Infrastructure (docker-compose)

```bash
cp .env.example .env
docker compose up -d   # PostgreSQL 16 (pgvector), Qdrant 1.12, Redis 7, Grafana
uv run alembic upgrade head   # apply SQL migrations after PG is up
```

PostgreSQL extension note: use `CREATE EXTENSION IF NOT EXISTS vector;` (not `pgvector`).

## Architecture in one sentence

Dual-channel hybrid search: lexical (PostgreSQL pg_trgm/tsvector) + vector (Qdrant bge-m3 1024-dim), synced via transactional outbox with background reconciler.

Key entry points:
- `app/main.py` → FastAPI app factory `create_app()`
- `app/config.py` → all config via pydantic-settings (env > `.env`), ~13 nested `BaseSettings` classes
- `app/reconciler/worker.py` → outbox poller, embeds + upserts to Qdrant
- `app/search/` → search orchestrator, lexical/vector channels, RRF fusion, speculative rerank
- `app/embedding/service.py` → bge-m3 embedding service (FlagEmbedding)
- `app/reranker/service.py` → cross-encoder reranker with circuit breaker

## Conventions

- **Async-only SQLAlchemy** (asyncpg). No sync engine anywhere.
- **No hardcoded ports/hosts** — everything through `app/config.py` pydantic-settings.
- **Migrations only via Alembic** — idempotent, with downgrade paths.
- **Pydantic `extra="forbid"`** on request/response models to prevent silent schema drift.
- **Structlog JSON logging** — every log entry carries `request_id`, `tenant_id`, `trace_id` via contextvars.
- **Code comments reference `ARCHITECT.md` sections** (e.g. "ARCHITECT 4.4") and roadmap items (P-07, C-03).

## Testing

- **pytest** with `asyncio_mode = "auto"` — async tests run without decorators.
- **Unit tests** (`tests/unit/`) use `fakeredis` and mocks — no Docker needed.
- **Integration tests** (`tests/integration/`) use `testcontainers` (PG + Redis) — Docker required.
- **E2E tests** (`tests/e2e/`) need full stack.
- Marker `slow` — for embedding model loads and docker-dependent tests.
- Run a single test: `uv run pytest tests/unit/test_search.py::test_name -v`

## Gotchas

- `uv.lock` is the lockfile — commit it when deps change.
- `.python-version` targets 3.12; `pyproject.toml` requires `>=3.11`.
- License mismatch: `LICENSE` file says Apache 2.0, `pyproject.toml` says MIT.
- `qdrant_collections.yaml` defines collection config declaratively — run `scripts/init_qdrant.py` to apply.
- Feature flags (`vector_search_enabled`, `rerank_enabled`, etc.) can disable components at runtime without redeploy.
- No CI workflows exist yet (`.github/workflows/` is empty).
