# OriaHS — гибридный поиск (PostgreSQL + Qdrant), фаза Critical

Бэкенд гибридного поиска: лексический канал (PostgreSQL 16 + pg_trgm + tsvector) и
векторный канал (Qdrant 1.x + bge-m3), синхронизированные через transactional
outbox с фоновым реконсилятором. Фаза Critical добавляет cross-encoder
rerank, circuit breaker, speculative rerank, веса в fusion, фасеты,
push-down фильтры, адаптивный throttle, degraded mode и ночной оффлайн-eval.

> Стек: Python 3.11+ (FastAPI, SQLAlchemy 2.x async), PostgreSQL 16, Qdrant 1.x, Redis 7, Prometheus 2, Grafana.
> Пакетный менеджер: **uv**.

## Быстрый старт

### 1. Поднять инфраструктуру

```bash
# bash / Git Bash / WSL
./scripts/infra_up.sh
# PowerShell
.\scripts\infra_up.ps1
```

Скрипт идемпотентен и выполняет весь путь от нуля до рабочего стека:

1. создаёт `.env` из `.env.example` (если его нет);
2. собирает образ приложения и поднимает контейнеры;
3. дожидается healthcheck'ов;
4. создаёт PG-расширения (`vector` — её нет ни в одной миграции);
5. накатывает миграции `alembic upgrade head` (включая `005_eval_datasets`, C-07);
6. создаёт коллекцию `documents` через `scripts/init_qdrant.py`;
7. проверяет `GET /health/ready` и печатает сводку URL.

Частые флаги:

```bash
./scripts/infra_up.sh --no-app            # только postgres, qdrant, redis, prometheus, grafana
./scripts/infra_up.sh --rebuild           # пересобрать образ без кэша
./scripts/infra_up.sh --seed-eval         # залить eval-корпус и дождаться синка в Qdrant
./scripts/infra_up.sh --seed-eval --nightly  # + прогнать ночной оффлайн-eval
```

Ручной вариант (без скрипта) — ровно те же шаги:

```bash
cp .env.example .env
docker compose --profile app up -d
docker compose run --rm migrate          # alembic upgrade head
docker compose run --rm init-qdrant      # scripts/init_qdrant.py
docker compose exec postgres psql -U postgres -d orlahs -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 2. Остановить инфраструктуру

```bash
./scripts/infra_down.sh          # откат схемы + остановка, тома сохраняются
./scripts/infra_down.sh -v       # то же, но тома удаляются (данные теряются)
./scripts/infra_down.sh -v --purge   # + удалить образ и build-кэш
```

`alembic downgrade base` выполняется **до** остановки контейнеров — иначе БД
недоступна и откат невозможен. Расширения PostgreSQL при откате сохраняются
(так и задумано в `001_documents.downgrade()`).

### 3. Установить зависимости и поднять API локально

Если нужны hot-reload и правки без пересборки образа:

```bash
uv sync                 # создаёт .venv и ставит зависимости
uv run uvicorn app.main:app --reload --port 8000
```

Проверить: `curl http://localhost:8000/health/live` → `{"status":"alive"}`.

## Порты и сервисы

| Сервис | Порт (`.env`) | Назначение |
|---|---|---|
| `api` | `API_PORT=8000` | REST API, `/metrics`, `/health/*` |
| `postgres` | `POSTGRES_PORT=5433` | документы, outbox, eval-датасеты |
| `qdrant` | `QDRANT_PORT=6333` | векторный индекс (gRPC `QDRANT_GRPC_PORT=6334`) |
| `redis` | `REDIS_PORT=6379` | кэш эмбеддингов, Streams-очередь |
| `prometheus` | `PROMETHEUS_PORT=9090` | источник данных дашбордов Grafana |
| `grafana` | `GRAFANA_PORT=3000` | 4 MVP + 4 CRITICAL дашборда |

> `POSTGRES_PORT` в `.env.example` равен 5433, потому что 5432 часто занят
> локальным PostgreSQL. Внутри compose-сети всегда `postgres:5432`.

## Профили compose

| Профиль | Сервисы | Когда нужен |
|---|---|---|
| — (по умолчанию) | `postgres`, `qdrant`, `redis`, `prometheus`, `grafana`, `migrate`, `init-qdrant` | инфраструктура и one-shot jobs |
| `app` | `api`, `reconciler`, `digest` | полный стек (по умолчанию в `infra_up.sh`) |
| `tools` | `seed-eval` | заливка eval-корпуса для C-07 |
| `nightly` | `nightly-eval` | ночной оффлайн-eval (C-07), разовый job |

`reconciler` и `digest` — отдельные процессы (`python -m app.reconciler.worker`
/ `python -m app.reconciler.digest`): наполнением Qdrant занимается
реконсилятор outbox, а не API.

## Наблюдаемость

- Prometheus скарпит `api:8000/metrics` и `qdrant:6333/metrics`
  (`monitoring/prometheus/prometheus.yml`), Grafana использует его как
  default-датасорс.
- CRITICAL-метрики: `reranker_latency_ms`, `circuit_breaker_state`,
  `circuit_breaker_opened_total`, `eval_recall_at_10`, `eval_ndcg_at_10`,
  `search_degraded_total`, `qdrant_pushdown_rate_total`, `outbox_throttled_total`
  (`app/observability/metrics.py`).

## Тесты и качество

```bash
uv run ruff check app/ tests/ scripts/
uv run mypy app/
uv run pytest
./scripts/run_dod_check.sh              # DoD MVP (P-17)
./scripts/run_dod_check_critical.sh     # DoD Critical (C-14) + NFR
```

## Структура

```
docker-compose.yml         # postgres + qdrant + redis + prometheus + grafana + app (профиль app)
Dockerfile                 # общий образ для api/reconciler/digest/one-shot jobs
monitoring/prometheus/     # prometheus.yml
scripts/infra_up.{sh,ps1}  # подъём стека
scripts/infra_down.{sh,ps1}# остановка + откат миграций
scripts/init_qdrant.py     # коллекция documents (ARCHITECT §17.1)
scripts/seed_eval_corpus.py# документы под eval-датасет (C-07)
pyproject.toml             # зависимости (uv)
.env.example               # все env-переменные с примерами
alembic/                   # SQL-миграции (async engine)
app/
  main.py                  # FastAPI app factory create_app()
  config.py                # pydantic-settings (env > .env)
  db/session.py            # async engine + session
  api/routes/              # index, search, health
  search/                  # orchestrator, lexical, vector, fusion, rerank, facets, pushdown
  reranker/                # cross-encoder, circuit breaker (C-01, C-03)
  eval/                    # nightly-eval (C-07)
  reconciler/              # outbox worker + dead-letter digest
grafana/provisioning/      # датасорсы + 8 дашбордов
tests/                     # unit / integration / e2e
```

## Соглашения

- Только async SQLAlchemy (asyncpg), никакого sync-движка.
- Все порты/хосты — через pydantic-settings, без хардкодов.
- Миграции — только через Alembic (идемпотентные, с downgrade).

Подробности: `analitics/ARCHITECT.md`, `analitics/ROADMAP.md`,
`analitics/MVP-PROMPTS.md`, `analitics/CRITICAL-PROMPTS.md`.
