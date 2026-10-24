# AGENTS.md — OriaHS hybrid search backend

## Quick commands

```bash
./scripts/infra_up.sh        # up the whole stack (PowerShell: .\scripts\infra_up.ps1)
./scripts/infra_down.sh      # stop it + alembic downgrade base (-v drops volumes)

uv sync                    # install deps into .venv
uv run uvicorn app.main:app --reload --port 8000   # dev server

uv run ruff check app/ tests/ scripts/   # lint
uv run ruff format app/ tests/  # format
uv run mypy app/                # typecheck (strict, pydantic plugin)
uv run pytest                   # all tests
uv run pytest tests/unit/       # unit only (no infra needed)
uv run pytest -m "not slow"     # skip slow/docker tests
```

Order matters: **lint → typecheck → test**.

### Verification recipes (pick one, don't re-discover)

```bash
# Fast CI-like check (no docker)
uv run ruff check app/ tests/ scripts/ && uv run mypy app/ && uv run pytest tests/unit/ -m "not slow"

# Single area (example: search)
uv run pytest tests/unit/test_search_orchestrator_test.py tests/unit/test_rrf_fusion_test.py -v

# Integration (needs docker)
uv run pytest tests/integration/ -m "not slow"
```

## Infrastructure (docker-compose)

```bash
./scripts/infra_up.sh                    # .env + build + up + extensions + alembic + init_qdrant + /health/ready
./scripts/infra_up.sh --no-app           # infra only (postgres, qdrant, redis, prometheus, grafana)
./scripts/infra_up.sh --seed-eval        # seed the eval corpus (C-07) and wait for the outbox
./scripts/infra_down.sh -v --purge       # full reset: volumes + image + build cache
```

Manual equivalent (what the scripts do, in order):

```bash
cp .env.example .env
docker compose --profile app up -d
docker compose exec postgres psql -U postgres -d orlahs -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose run --rm migrate        # alembic upgrade head
docker compose run --rm init-qdrant    # scripts/init_qdrant.py
```

Compose profiles: default (infra + one-shot `migrate`/`init-qdrant`), `app`
(`api`, `reconciler`, `digest`), `tools` (`seed-eval`), `nightly`
(`nightly-eval`, one-shot). Prometheus (`monitoring/prometheus/prometheus.yml`)
is the datasource behind all 8 Grafana dashboards.

PostgreSQL extension note: use `CREATE EXTENSION IF NOT EXISTS vector;` (not `pgvector`).

### Windows / PowerShell notes (this machine is win32)

- Prefer `uv run ...` / `docker compose ...` directly; `scripts/*.sh` often fail under plain PowerShell.
- Use `.\scripts\infra_up.ps1` / `.\scripts\infra_down.ps1` when a script wrapper is needed.
- **Never read source files via `Get-Content` / `Select-String`** — use the native Read / Grep / Glob tools.
- Avoid absolute-path composition (`docker compose --project-directory C:\projects\OriaHS ...`); run from repo root with relative paths.
- Alembic: `uv run alembic heads` → current head is `012_tenant_search_stats`.

## Architecture in one sentence

Dual-channel hybrid search: lexical (PostgreSQL pg_trgm/tsvector) + vector (Qdrant bge-m3 1024-dim), synced via transactional outbox with background reconciler.

Key entry points:
- `app/main.py` → FastAPI app factory `create_app()`
- `app/config.py` → all config via pydantic-settings (env > `.env`), ~13 nested `BaseSettings` classes
- `app/reconciler/worker.py` → outbox poller, embeds + upserts to Qdrant
- `app/search/` → search orchestrator, lexical/vector channels, RRF fusion, speculative rerank
- `app/embedding/service.py` → bge-m3 embedding service (FlagEmbedding)
- `app/reranker/service.py` → cross-encoder reranker with circuit breaker

### Hot modules (read only what you need — do not re-explore the whole tree)

| Path | What lives there |
|---|---|
| `app/search/orchestrator.py` | retrieve→fuse→rerank pipeline; personalization, adaptive_k, speculative hooks |
| `app/search/fusion.py` | RRF / weighted fusion |
| `app/search/lexical.py`, `vector.py` | channel implementations |
| `app/api/routes/search.py` | `POST /search`, `/click`, degraded-mode responses |
| `app/api/schemas.py` | request/response models (`extra="forbid"`) |
| `app/config.py` | all settings; search by nested class name (e.g. `FeatureFlags`) |
| `app/services/personalization.py` | EMA profiles, GDPR delete |
| `app/observability/metrics.py` | Prometheus counters/histograms |
| `app/db/queries/` | raw-SQL async helpers (outbox, users, search_events) |
| `alembic/versions/` | migrations; next free revision after `012` |

## Docs map (`analitics/`) — open only the section you need

| File | Use when |
|---|---|
| `analitics/ARCHITECT.md` | Source of truth (1642 lines). **Read only the §ref cited in code comments / prompts** — never the whole file. |
| `analitics/BACKLOG.md` | Status tracking for P-xx / C-xx / PR-xx / IM-xx; update status after DoD. |
| `analitics/PROD-PROMPTS.md` | Specs for Production-Ready tasks PR-01..PR-15. |
| `analitics/MVP-PROMPTS.md` | Historical MVP phase (P-00..P-17); open only for bugfixes. |
| `analitics/CRITICAL-PROMPTS.md` | Historical Critical phase (C-00..C-14); open only for bugfixes. |
| `analitics/BUGFIXES-PROMPTS.md` | Post-review defect prompts. |
| `analitics/ROADMAP.md` | Phase / NFR targets; cross-check with BACKLOG. |

## Conventions

- **Async-only SQLAlchemy** (asyncpg). No sync engine anywhere.
- **No hardcoded ports/hosts** — everything through `app/config.py` pydantic-settings.
- **Migrations only via Alembic** — idempotent, with downgrade paths.
- **Pydantic `extra="forbid"`** on request/response models to prevent silent schema drift.
- **Structlog JSON logging** — every log entry carries `request_id`, `tenant_id`, `trace_id` via contextvars.
- **Code comments reference `ARCHITECT.md` sections** (e.g. "ARCHITECT 4.4") and roadmap items (P-07, C-03).

### Alembic workflow (3 commands)

```bash
uv run alembic heads                 # what is head right now
uv run alembic revision -m "013_..." # create new revision (idempotent + downgrade)
uv run alembic upgrade head          # apply
# Extension: CREATE EXTENSION IF NOT EXISTS vector;  (never "pgvector")
```

## Testing

- **pytest** with `asyncio_mode = "auto"` — async tests run without decorators.
- **Unit tests** (`tests/unit/`) use `fakeredis` and mocks — no Docker needed.
- **Integration tests** (`tests/integration/`) use `testcontainers` (PG + Redis) — Docker required.
- **E2E tests** (`tests/e2e/`) need full stack.
- Marker `slow` — for embedding model loads and docker-dependent tests.
- Naming: files are `test_<area>_test.py` (note the double `_test`); follow existing pattern when adding.
- Run a single test: `uv run pytest tests/unit/test_search_orchestrator_test.py::test_name -v`

## Session / work hygiene

- Prefer **short cycles**: one PR/feature → lint → typecheck → test → commit (when asked).
- Before a long task, write a plan under `.opencode/plans/` and execute it in a **new session** (don't extend a 500+ step session).
- For independent tasks, open a fresh session instead of branching inside an exhausted one.
- `git status --short` before assuming a clean baseline — worktree may already have dirty files from a prior PR.

## Gotchas

- `uv.lock` is the lockfile — commit it when deps change.
- `.python-version` targets 3.12; `pyproject.toml` requires `>=3.11`.
- License mismatch: `LICENSE` file says Apache 2.0, `pyproject.toml` says MIT.
- `qdrant_collections.yaml` defines collection config declaratively — run `scripts/init_qdrant.py` to apply.
- Feature flags (`vector_search_enabled`, `rerank_enabled`, etc.) can disable components at runtime without redeploy.
- No CI workflows exist yet (`.github/workflows/` is empty).
