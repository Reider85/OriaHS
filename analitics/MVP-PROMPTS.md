# MVP-PROMPTS — Промпты для ИИ-агента (Claude Code) по реализации Фазы MVP гибридного поиска

> **Источник**: `ARCHITECT.md` v2.0 (TRIZ-applied) + `ROADMAP.md` v1.0
> **Целевая фаза**: MVP (ROADMAP §3) — минимальный рабочий контур end-to-end гибридного поиска
> **Стек**: Python (FastAPI), PostgreSQL 16, Qdrant 1.x, Redis 7
> **Целевой агент**: Claude Code
> **Стиль промптов**: spec-driven (Context / Goal / Constraints / References / Acceptance Criteria / Forbidden patterns / Suggested files)
> **Гранулярность**: 1 промпт = 1 задача (~30–90 минут работы агента)

---

## 0. Как пользоваться этим документом

### 0.1. Порядок выполнения

Промпты пронумерованы `P-00` … `P-17`. Большинство из них имеет **upstream-зависимости** — выполнение `P-07` (POST /index) бессмысленно без готовых `P-01`, `P-02`, `P-03`, `P-05`. Параллельно можно запускать только промпты из разных подсистем (например, `P-04` Qdrant и `P-01`–`P-03` PostgreSQL).

Рекомендуемый порядок:

1. **P-00** (Bootstrap) — обязательно первым.
2. Параллельно: **P-01** → **P-02** → **P-03** (миграции PG) и **P-04** (Qdrant-коллекция).
3. Параллельно: **P-05** → **P-06** (Embedding Service).
4. Последовательно: **P-07** → **P-08** (Index API).
5. Параллельно: **P-09**, **P-10** (каналы поиска) → **P-11** (orchestrator) → **P-12** (health/metrics).
6. Параллельно: **P-13** (Reconciler) и **P-14** (Digest-воркер).
7. Параллельно: **P-15** (метрики/логи) и **P-16** (Grafana).
8. **P-17** (E2E-тесты и приёмка DoD) — последним, после готовности всех компонентов.

### 0.2. Карта промптов

| ID | Компонент ROADMAP | Зависимости | ARCHITECT §ref | ROADMAP §ref | Ожидаемый артефакт |
|----|---|---|---|---|---|
| **P-00** | Bootstrap | — | §2.1, §17.2 | §3 (вводная) | `docker-compose.yml`, `pyproject.toml`, `app/` skeleton, `.env.example`, alembic init |
| **P-01** | PG `documents` | P-00 | §3.1 | §3.2.1 | Alembic-миграция `001_documents`, `app/db/models/document.py` |
| **P-02** | PG `search_outbox` | P-01 | §3.1, §4.4 | §3.2.1 | Миграция `002_outbox`, `app/db/models/outbox.py`, индексы |
| **P-03** | PG `embedding_models` | P-01 | §3.1, §5.4 | §3.2.1, §3.3 | Миграция `003_embedding_models` + seed `bge-m3-v1` |
| **P-04** | Qdrant collection | P-00 | §3.2, §17.1 | §3.2.2 | `qdrant_collections.yaml` + `scripts/init_qdrant.py` |
| **P-05** | Embedding model | P-00, P-03 | §5.1, §5.2 | §3.2.3 | `app/embedding/service.py` (bge-m3 + батчинг 32) |
| **P-06** | Embedding cache | P-05 | §4.3, §5.3 | §3.2.3, §3.3 | `app/embedding/cache.py` (Redis hash + qemb + fast-path) |
| **P-07** | POST /index | P-01..P-03, P-06 | §2.4, §4.2, §14.2 | §3.2.4 | `app/api/routes/index.py` (create + outbox + Redis Streams) |
| **P-08** | DELETE /index/{doc_id} | P-01, P-02, P-07 | §14.4 | §3.2.4 | soft-delete + outbox `op='delete'` |
| **P-09** | Lexical search | P-01 | §6.1, §8.1, §8.3 | §3.2.4 | `app/search/lexical.py` (ts_rank + pg_trgm + filters, K=50) |
| **P-10** | Vector search | P-04, P-06 | §6.1, §8.3 | §3.2.4 | `app/search/vector.py` (Qdrant kNN + payload filter, K=50) |
| **P-11** | POST /search orchestrator | P-09, P-10 | §2.3, §6.1, §7.1, §14.1 | §3.2.4 | `app/api/routes/search.py` + `app/search/fusion.py` (RRF k=60) |
| **P-12** | Health + /metrics | P-01..P-04 | §14.5, §11.1 | §3.2.4, §3.2.6 | `app/api/routes/health.py`, `/metrics`, readiness check |
| **P-13** | Reconciler | P-02, P-04..P-06 | §4.4, §4.5 | §3.2.5 | `app/reconciler/worker.py` (30s poll, retry, dead) |
| **P-14** | Digest-воркер | P-02, P-13 | §4.4 | §3.2.5, §3.3 | `app/reconciler/digest.py` + миграция `004_dead_digest` |
| **P-15** | Observability baseline | P-12 | §11.1, §11.4 | §3.2.6 | `app/observability/metrics.py`, structured JSON logger |
| **P-16** | Grafana dashboards | P-15 | §11.1 | §3.2.6 | `grafana/provisioning/*.json` (4 дашборда) |
| **P-17** | E2E + DoD | все предыдущие | §3.4, §3.5 (DoD) | §3.4, §3.5 | `tests/e2e/test_mvp_dod.py`, `scripts/run_dod_check.sh` |

### 0.3. Глобальные правила для агента

1. **Перед стартом каждого промпта** агент обязан прочитать соответствующие §ref из `ARCHITECT.md` и `ROADMAP.md` (файлы лежат в корне репозитория). Если в промпте указано `ARCHITECT §3.1` — открыть `ARCHITECT.md`, найти раздел `### 3.1`, прочитать целиком.
2. **Не выходить за рамки MVP**. Любые фичи из §4–§6 ROADMAP (cross-encoder, weighted fusion, push-down, circuit breaker, degraded mode, OTel tracing, A/B, персонализация) — **запрещены** в этой фазе. Если промпт молчит про фичу — её не реализовывать.
3. **TRIZ-gates встраиваются с первого дня** (ROADMAP §3.3). Их нельзя «отложить на потом»:
   - `dead`-состояние в `search_outbox` — обязательно в P-02.
   - Digest-агрегатор dead-записей — обязательно P-14.
   - Fast-path по `content_hash` — обязательно в P-06 и P-07.
   - `is_active` в `embedding_models` — обязательно в P-03.
   - Структура `search_events` (партиционирование заложить) — создание пустой партиционированной таблицы допустимо в P-01, но не заполняется.
4. **Единый `QdrantPayload`** — pydantic-модель, описывающая payload точки Qdrant. Должна быть определена один раз (в P-04) и переиспользована в P-07, P-10, P-13. Schema drift между PG и Qdrant = баг.
5. **Все SQL-миграции** — через Alembic, идемпотентные, с `downgrade()`. Никаких ручных `psql`-правок в БД.
6. **Все env-dependent параметры** (хосты, порты, модельные имена) — через `pydantic-settings` (`app/config.py`), никаких хардкодов.
7. **Self-check перед завершением промпта**:
   - `ruff check app/ tests/` — без ошибок.
   - `mypy app/` — без ошибок (strict-режим для нового кода).
   - `pytest tests/unit/<module>_test.py` — зелёный.
   - Если промпт добавляет endpoint: `curl`-проверка против локально поднятого `docker-compose up`.
8. **После завершения промпта** — краткий отчёт: что создано, какие §ref закрыты, какие отклонения от спеки (если есть) и почему.

### 0.4. Соглашения по структуре репозитория

```
.
├── ARCHITECT.md                # источник истины (v2.0)
├── ROADMAP.md                  # источник фаз (v1.0)
├── docker-compose.yml          # P-00
├── pyproject.toml              # P-00
├── .env.example                # P-00
├── alembic.ini                 # P-00
├── alembic/
│   └── versions/               # P-01, P-02, P-03, P-14
├── app/
│   ├── __init__.py
│   ├── config.py               # P-00 (pydantic-settings)
│   ├── main.py                 # P-00 (FastAPI app factory)
│   ├── api/
│   │   ├── routes/
│   │   │   ├── index.py        # P-07, P-08
│   │   │   ├── search.py       # P-11
│   │   │   └── health.py       # P-12
│   │   └── deps.py             # P-00
│   ├── db/
│   │   ├── session.py          # P-00
│   │   └── models/             # SQLAlchemy models
│   ├── embedding/
│   │   ├── service.py          # P-05
│   │   └── cache.py            # P-06
│   ├── search/
│   │   ├── lexical.py          # P-09
│   │   ├── vector.py           # P-10
│   │   └── fusion.py           # P-11
│   ├── reconciler/
│   │   ├── worker.py           # P-13
│   │   └── digest.py           # P-14
│   └── observability/
│       ├── metrics.py          # P-15
│       └── logging.py          # P-15
├── qdrant_collections.yaml     # P-04
├── scripts/
│   ├── init_qdrant.py          # P-04
│   └── run_dod_check.sh        # P-17
├── grafana/provisioning/       # P-16
└── tests/
    ├── unit/
    └── e2e/
        └── test_mvp_dod.py     # P-17
```

---

## P-00 — Bootstrap проекта

> **Компонент ROADMAP §3 (вводная)** | **Артефакт**: рабочая локальная среда `docker-compose up` + skeleton FastAPI-приложения

### Context

Проект начинается с нуля. Цель фазы MVP (ROADMAP §3.1) — доставить end-to-end работающий гибридный поиск за 2–3 недели. Перед реализацией любых компонентов нужно поднять инфраструктуру (PostgreSQL 16 + расширения, Qdrant 1.x, Redis 7) и каркас приложения, чтобы все последующие промпты могли опираться на готовые docker-compose, pydantic-settings, alembic и структуру каталогов.

### Goal

Создать минимальный рабочий репозиторий: `docker-compose.yml` со всеми тремя зависимостями, `pyproject.toml` с зависимостями (FastAPI, SQLAlchemy 2.x async, asyncpg, alembic, qdrant-client, redis, pydantic v2, pydantic-settings, sentence-transformers, prometheus-fastapi-instrumentator, ruff, mypy, pytest, pytest-asyncio, testcontainers), skeleton `app/` с FastAPI app factory, `pydantic-settings`-конфиг, `.env.example`, `alembic init alembic/`.

### Constraints

- PostgreSQL 16 + расширения `pg_trgm`, `pgvector` (для `users.profile_vector` в будущем), `uuid-ossp` (или `gen_random_uuid()` из `pgcrypto`).
- Redis 7 (не 6 — нужен `XADD` с `NOMKSTREAM` иStreams consumer groups).
- Qdrant 1.x (последний стабильный).
- Python 3.11+.
- Все порты вынести в `.env`: `POSTGRES_PORT=5432`, `QDRANT_PORT=6333`, `QDRANT_GRPC_PORT=6334`, `REDIS_PORT=6379`, `API_PORT=8000`.
- `pydantic-settings` читает из `.env` + из окружения (priority: env > .env).
- `app/main.py` — FastAPI app factory (`create_app() -> FastAPI`), не глобальный `app = FastAPI()`.
- Health-endpoints пока заглушки (P-12 наполнит), но роутер должен существовать.

### References

- **ARCHITECT §2.1** — список компонентов системы (PostgreSQL, Qdrant, Embedding Worker, Redis, API).
- **ARCHITECT §17.2** — справочник `config.yaml` приложения (включая секции `embedding`, `search`, `reconciler`, `observability`). Использовать как основу для `app/config.py`.
- **ROADMAP §3** — фаза MVP, общий контекст.

### Acceptance Criteria

- `docker-compose up -d` поднимает 3 контейнера (postgres, qdrant, redis), все проходят healthcheck.
- `docker-compose exec postgres psql -U postgres -c "CREATE EXTENSION pg_trgm; CREATE EXTENSION pgvector; CREATE EXTENSION pgcrypto;"` выполняется без ошибок.
- `uvicorn app.main:app --reload` стартует, `GET /` возвращает `{"status": "ok"}` (временный root-эндпоинт, удалится в P-12).
- `alembic upgrade head` выполняется без ошибок (пока без миграций — `alembic init` создаёт пустую `alembic/versions/`).
- `ruff check app/` — зелёный.
- `mypy app/` — зелёный (минимальная конфигурация, strict на новых файлах).
- `pytest` — зелёный (один smoke-тест на `create_app()`).
- `.env.example` содержит все переменные из `app/config.py` с примерами значений.

### Forbidden patterns

- Не использовать глобальные `app = FastAPI()` — только factory.
- Не хардкодить хосты/порты — только через `pydantic-settings`.
- Не использовать `requirements.txt` — только `pyproject.toml` с `uv` или `poetry` (на выбор, зафиксировать в `README.md`).
- Не добавлять фичи из Roadmap §4+ (reranker, weighted fusion, A/B, OTel).
- Не использовать `sqlalchemy` sync-движок — только `async_sessionmaker` + `asyncpg`.

### Suggested files

- `docker-compose.yml`
- `pyproject.toml`
- `.env.example`
- `alembic.ini`
- `alembic/env.py`
- `alembic/script.py.mako`
- `app/__init__.py`
- `app/main.py`
- `app/config.py`
- `app/api/deps.py`
- `app/db/session.py`
- `tests/conftest.py`
- `tests/test_smoke.py`
- `README.md` (краткое: как поднять, как запустить тесты)

---

## P-01 — PostgreSQL: таблица `documents`

> **Компонент ROADMAP §3.2.1** | **Артефакт**: Alembic-миграция + SQLAlchemy-модель

### Context

`documents` — source of truth гибридного поиска. Все остальные подсистемы (outbox, embedding, search, reconciler) опираются на эту таблицу. Колонка `content_hash` — критическая для идемпотентности (TRIZ-gate из ROADMAP §3.3). Колонка `tsv tsvector GENERATED ALWAYS` — для лексического канала поиска.

### Goal

Создать Alembic-миграцию `001_documents`, которая создаёт таблицу `documents` со всеми колонками и индексами из спецификации, плюс SQLAlchemy 2.x модель `Document` в `app/db/models/document.py`.

### Constraints

- Колонки (точные имена и типы — из ARCHITECT §3.1):
  - `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
  - `tenant_id UUID NOT NULL`
  - `external_ref TEXT NOT NULL`
  - `title TEXT NOT NULL`
  - `content TEXT NOT NULL`
  - `language TEXT NOT NULL` (ISO 639-1)
  - `tags TEXT[] NOT NULL DEFAULT '{}'`
  - `attributes JSONB NOT NULL DEFAULT '{}'`
  - `embedding_model TEXT NOT NULL`
  - `embedding_rev INT NOT NULL DEFAULT 1`
  - `content_hash TEXT NOT NULL` (sha256(title + "\n" + content))
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `deleted_at TIMESTAMPTZ` (nullable, для soft-delete)
  - `UNIQUE (tenant_id, external_ref)`
- `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(content,''))) STORED`
- Индексы (имена — из ARCHITECT §3.1, не менять):
  - `documents_tsv_idx` — `GIN (tsv)`
  - `documents_trgm_idx` — `GIN (title gin_trgm_ops, content gin_trgm_ops)`
  - `documents_tenant_lang_idx` — `(tenant_id, language) WHERE deleted_at IS NULL`
  - `documents_attrs_gin_idx` — `GIN (attributes jsonb_path_ops)`
  - `documents_tags_gin_idx` — `GIN (tags)`
- Расширения `pg_trgm`, `pgcrypto` (для `gen_random_uuid()`) должны быть созданы **в этой же миграции** через `op.execute("CREATE EXTENSION IF NOT EXISTS ...")`.
- SQLAlchemy-модель — асинхронная, с явными `Mapped[...]` аннотациями.
- `updated_at` должен автообновляться при UPDATE — триггер в миграции или SQLAlchemy-серверный default `now()`.
- Создать **пустую партиционированную** таблицу `search_events` (TRIZ-gate ROADMAP §3.3 — структура закладывается на MVP, партиционирование наполняется в Production-Ready): `CREATE TABLE search_events (...) PARTITION BY RANGE (created_at);` + создать партицию по умолчанию `search_events_default`.

### References

- **ARCHITECT §3.1** — полная SQL-схема `documents`, `search_outbox`, `tenant_search_stats`, `users`, `search_events`, `embedding_models`. Таблицы `tenant_search_stats`, `users` в MVP **не создаются** (появятся в Production-Ready), но `search_events` создаётся как пустая партиционированная структура.
- **ROADMAP §3.2.1** — перечень полей и индексов `documents`.
- **ROADMAP §3.3** — TRIZ-gate «Партиционирование `search_events` (заложить структуру)».

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу `documents` со всеми колонками и индексами.
- `alembic downgrade base` — откатывает чисто (DROP TABLE + DROP EXTENSION не делать — расширения оставляем, чтобы не ломать другие тесты; DROP только таблиц).
- `\d documents` в psql показывает все колонки, индексы и constraint `documents_tenant_id_external_ref_key`.
- `\d+ documents` показывает `tsv` как generated stored column.
- `app/db/models/document.py` содержит `class Document(Base):` со всеми полями, тип `UUID` для `id`/`tenant_id`, `ARRAY(Text)` для `tags`, `JSONB` для `attributes`.
- Unit-тест `tests/unit/test_document_model_test.py`: создаёт `Document(...)`, вставляет, читает обратно, проверяет что `content_hash` сохранён, `tsv` populated (через `SELECT tsv FROM documents WHERE id=...`).

### Forbidden patterns

- Не использовать `sqlalchemy_utils.UUIDType` — только встроенный `sqlalchemy.Uuid` (SQLAlchemy 2.x).
- Не добавлять колонки вне спеки (никаких `author_id`, `version`, `is_published`).
- Не создавать `tenant_search_stats` и `users` в этой миграции.
- Не использовать `Text` для `tenant_id` — только `UUID`.
- Не забывать `WHERE deleted_at IS NULL` в partial-индексе `documents_tenant_lang_idx`.

### Suggested files

- `alembic/versions/001_documents.py`
- `app/db/models/__init__.py`
- `app/db/models/document.py`
- `app/db/models/base.py` (если ещё нет — `class Base(DeclarativeBase): ...`)
- `tests/unit/test_document_model_test.py`

---

## P-02 — PostgreSQL: таблица `search_outbox` + индексы

> **Компонент ROADMAP §3.2.1, §3.2.5** | **Артефакт**: Alembic-миграция + модель `OutboxItem`

### Context

`search_outbox` — сердце dual-write паттерна (ARCHITECT §4). Без неё нет eventually consistent синхронизации между Postgres и Qdrant. Состояние `dead` (TRIZ-gate ROADMAP §3.3) — обязательно с первого дня, иначе вечные ретраи забьют `SELECT` в reconciler'е.

### Goal

Создать миграцию `002_outbox`, создающую таблицу `search_outbox` со всеми колонками, `CHECK`-констрейнтами на `op` и `status`, и двумя индексами. Реализовать SQLAlchemy-модель `OutboxItem`.

### Constraints

- Колонки (из ARCHITECT §3.1):
  - `id BIGSERIAL PRIMARY KEY`
  - `document_id UUID NOT NULL REFERENCES documents(id)`
  - `op TEXT NOT NULL CHECK (op IN ('upsert','delete'))`
  - `status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','done','failed','dead'))`
  - `attempts INT NOT NULL DEFAULT 0`
  - `last_error TEXT` (nullable)
  - `next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `content_hash TEXT` (nullable — snapshot хэша на момент создания outbox-записи)
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Индексы (имена — из ARCHITECT §3.1):
  - `search_outbox_pending_idx` — `(next_retry_at) WHERE status IN ('pending','failed')` (partial index — критично для производительности reconciler'а).
  - `search_outbox_doc_idx` — `(document_id, status)`.
- `updated_at` триггер — автообновление при UPDATE.
- Состояние `dead` — **обязательно** в `CHECK` (TRIZ-gate).

### References

- **ARCHITECT §3.1** — SQL-определение `search_outbox`.
- **ARCHITECT §4.4** — алгоритм работы reconciler (какие поля и статусы он использует).
- **ARCHITECT §4.5** — таблица гарантий согласованности (включая `dead` как safety net).
- **ROADMAP §3.2.1** — перечень полей и индексов.
- **ROADMAP §3.2.5** — уточнения по reconciler (интервал 30с, batch 500, `attempts > 20 → dead`).
- **ROADMAP §3.3** — TRIZ-gate «`dead`-состояние в outbox».

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу со всеми констрейнтами и индексами.
- `\d search_outbox` показывает `CHECK (status IN ('pending','in_progress','done','failed','dead'))`.
- `INSERT INTO search_outbox (document_id, op, status) VALUES (<id>, 'invalid_op', 'pending')` — падает с `CheckViolation`.
- `INSERT INTO search_outbox (document_id, op, status) VALUES (<id>, 'upsert', 'invalid_status')` — падает с `CheckViolation`.
- Partial-индекс `search_outbox_pending_idx` виден в `\di` с условием `WHERE status IN ('pending','failed')`.
- Unit-тест: вставка + чтение по `id`, проверка дефолтов (`status='pending'`, `attempts=0`, `next_retry_at≈now()`).

### Forbidden patterns

- Не использовать `INTEGER` для `id` — только `BIGSERIAL` (attempts могут накапливаться до миллионов).
- Не забыть `WHERE status IN ('pending','failed')` в partial-индексе — без него индекс будет сканировать всё.
- Не добавлять колонки `worker_id`, `lease_until` — это Improvements фаза, не MVP.
- Не использовать `TIMESTAMP` безTZ — только `TIMESTAMPTZ`.

### Suggested files

- `alembic/versions/002_outbox.py`
- `app/db/models/outbox.py`
- `tests/unit/test_outbox_model_test.py`

---

## P-03 — PostgreSQL: таблица `embedding_models` + seed

> **Компонент ROADMAP §3.2.1, §3.3** | **Артефакт**: миграция + модель + seed-скрипт

### Context

Таблица `embedding_models` — реестр доступных embedding-моделей. Колонка `is_active` (TRIZ-gate ROADMAP §3.3) готовит почву для zero-downtime миграций моделей в Production-Ready — без неё пришлось бы удалять модели физически. На MVP в таблице одна запись: `bge-m3-v1`.

### Goal

Создать миграцию `003_embedding_models` + seed с дефолтной моделью `bge-m3-v1` (dim=1024, `is_default=true`, `is_active=true`). Реализовать SQLAlchemy-модель `EmbeddingModel`.

### Constraints

- Колонки (из ARCHITECT §3.1):
  - `name TEXT PRIMARY KEY` (например, `'bge-m3-v1'`)
  - `dimension INT NOT NULL`
  - `description TEXT` (nullable)
  - `is_default BOOLEAN NOT NULL DEFAULT FALSE`
  - `is_active BOOLEAN NOT NULL DEFAULT TRUE`
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Seed-данные (в той же миграции через `op.bulk_insert` или raw `INSERT`):
  - `('bge-m3-v1', 1024, 'BAAI/bge-m3 multilingual dense embeddings', true, true, now())`
- `is_default=true` должен быть **уникальным** среди активных моделей — добавить partial unique index: `CREATE UNIQUE INDEX embedding_models_one_default_idx ON embedding_models (is_default) WHERE is_default = true AND is_active = true;`
- Модель `EmbeddingModel` в `app/db/models/embedding_model.py`.
- Хелпер `get_default_model(session) -> EmbeddingModel` в `app/db/queries/embedding_models.py` — кэшируется на уровне процесса (lru_cache с TTL через `cachetools.TTLCache`).

### References

- **ARCHITECT §3.1** — SQL-определение `embedding_models`.
- **ARCHITECT §5.1** — выбор модели (bge-m3, dim=1024, MIT).
- **ARCHITECT §5.4** — версионирование: `model_name` + `model_rev` сохраняются в payload Qdrant и в `documents.embedding_model`/`embedding_rev`.
- **ROADMAP §3.2.1** — описание таблицы.
- **ROADMAP §3.3** — TRIZ-gate «`is_active` в `embedding_models`».

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу, индекс `embedding_models_one_default_idx` и вставляет seed-запись.
- `SELECT name, dimension, is_default, is_active FROM embedding_models;` возвращает ровно одну строку `bge-m3-v1, 1024, true, true`.
- Попытка `INSERT` второй записи с `is_default=true` падает с `UniqueViolation` (partial unique index работает).
- `app/db/queries/embedding_models.py::get_default_model()` возвращает объект `EmbeddingModel` с `name='bge-m3-v1'`.
- Unit-тест покрывает: seed вставлен, partial-unique-index работает, `get_default_model` кэшируется (второй вызов не делает SQL — через `pytest-mock` spy на `session.execute`).

### Forbidden patterns

- Не добавлять поле `endpoint_url` — на MVP инференс локальный.
- Не создавать `model_file_path` — путь определяется в `app/config.py`, не в БД.
- Не забывать partial unique index на `is_default` — без него можно случайно создать две дефолтные модели.
- Не использовать `SELECT * FROM embedding_models WHERE is_default=true` без `is_active=true` — мягко удалённая модель не должна быть дефолтом.

### Suggested files

- `alembic/versions/003_embedding_models.py`
- `app/db/models/embedding_model.py`
- `app/db/queries/__init__.py`
- `app/db/queries/embedding_models.py`
- `tests/unit/test_embedding_models_test.py`

---

## P-04 — Qdrant: коллекция `documents`

> **Компонент ROADMAP §3.2.2** | **Артефакт**: YAML-конфиг + init-скрипт + pydantic `QdrantPayload`

### Context

Qdrant-коллекция `documents` — векторный канал гибридного поиска. На MVP — одна коллекция, одна модель (`bge-m3-v1`, 1024 dim). Quantization scalar int8 **закладывается сразу** (TRIZ-gate): retrospective изменение потребует полного reindex, который недопустим на MVP-тапе.

### Goal

Создать `qdrant_collections.yaml` (декларативное описание коллекции — ARCHITECT §17.1) + `scripts/init_qdrant.py` (idempotent-скрипт, создающий коллекцию и payload-индексы из YAML) + pydantic-модель `QdrantPayload` в `app/search/qdrant_payload.py` (единая для всех компонентов, чтобы избежать schema drift).

### Constraints

- Конфиг коллекции (из ARCHITECT §3.2, §17.1):
  - `vectors.size = 1024`, `distance = Cosine`
  - `shard_number = 4`, `replication_factor = 2` (для dev — `replication_factor = 1`, через env-override)
  - `write_consistency = majority`
  - `on_disk_payload = true`
  - `hnsw_config`: `m=16`, `ef_construct=200`, `full_scan_threshold=10000`
  - `optimizers_config`: `default_segment_number=4`, `indexing_threshold=20000`
  - `quantization_config.scalar`: `type=int8`, `quantile=0.99`, `always_ram=true`
- Payload точки (имена полей — из ARCHITECT §3.2):
  - `doc_id` (uuid-строка)
  - `tenant_id` (uuid-строка)
  - `language` (string)
  - `tags` (list[str])
  - `attributes` (dict — но индексируется только `attributes.category` и `attributes.price`)
  - `model_name` (string)
  - `model_rev` (int)
  - `created_at` (datetime, ISO-строка в Qdrant)
  - `content_hash` (string)
- Payload-индексы (имена полей + типы — из ARCHITECT §3.2):
  - `tenant_id` — keyword
  - `language` — keyword
  - `tags` — keyword (массив)
  - `attributes.category` — keyword
  - `attributes.price` — float
  - `model_name` — keyword
  - `model_rev` — keyword (не float — нужен exact match)
  - `created_at` — datetime
- `QdrantPayload` (pydantic v2) — с `model_config = ConfigDict(extra='forbid')`, чтобы предотвратить добавление новых полей без явной декларации.
- `scripts/init_qdrant.py` — идемпотентный: если коллекция существует, сравнивает конфиг и warns при расхождениях (но не перезаписывает — для этого нужен reindex, см. ARCHITECT §13).

### References

- **ARCHITECT §3.2** — полная JSONC-спецификация коллекции, payload и индексов.
- **ARCHITECT §17.1** — YAML-форма конфига (`qdrant_collections.yaml`).
- **ROADMAP §3.2.2** — краткий перечень параметров коллекции.
- **ROADMAP §3.6** — риск «Schema drift между Postgres и Qdrant»: mitigation — единый pydantic `QdrantPayload`.

### Acceptance Criteria

- `python scripts/init_qdrant.py` создаёт коллекцию `documents` в локальном Qdrant (через `docker-compose exec` или напрямую к `localhost:6333`).
- Повторный запуск `init_qdrant.py` — идемпотентен (no-op если конфиг совпадает, warns если расходится).
- `GET /collections/documents` через Qdrant REST API показывает все параметры из спеки (через `curl http://localhost:6333/collections/documents | jq`).
- `QdrantPayload` pydantic-модель валидирует тестовый payload из ARCHITECT §3.2 (с теми же полями) без ошибок.
- `QdrantPayload(extra_field='x')` падает с `ValidationError` (extra='forbid' работает).
- Unit-тест на `QdrantPayload` покрывает: валидный payload, пустые `tags`, `attributes={}`, `model_rev=0` (минимальное), `created_at` в ISO-формате.

### Forbidden patterns

- Не использовать `on_disk_payload=false` — для dev допустимо, но в проде payload может быть большим.
- Не откладывать `quantization_config` на потом — она **обязательна** с первого дня (TRIZ-gate).
- Не использовать `keyword` для `attributes.price` — он должен быть `float`, иначе range-фильтры не работают.
- Не забыть `model_rev` как keyword — для exact match при миграциях.
- Не хардкодить параметры коллекции в `init_qdrant.py` — только чтение из `qdrant_collections.yaml`.

### Suggested files

- `qdrant_collections.yaml`
- `scripts/init_qdrant.py`
- `app/search/__init__.py`
- `app/search/qdrant_payload.py`
- `app/search/qdrant_client.py` (фабрика `QdrantClient` из `app/config.py`)
- `tests/unit/test_qdrant_payload_test.py`
- `tests/unit/test_init_qdrant_test.py` (через `qdrant-client` + mock или testcontainers)

---

## P-05 — Embedding Service: модель `bge-m3` + батчинг

> **Компонент ROADMAP §3.2.3** | **Артефакт**: `app/embedding/service.py`

### Context

Embedding Service — общая инфраструктура для indexing (P-07) и search (P-10/P-11). Модель `BAAI/bge-m3` выбрана в ARCHITECT §5.1 как дефолт: 1024-мерная, мультиязычная, MIT-лицензия. Батчинг 32 — баланс между throughput и latency (ARCHITECT §5.2: ~800 embeddings/sec на A10g GPU, ~15/sec на CPU).

### Goal

Реализовать класс `EmbeddingService` с методами `embed_texts(texts: list[str]) -> np.ndarray` и `embed_query(query: str) -> np.ndarray`. Загрузка модели — lazy (при первом вызове), чтобы не тормозить старт приложения. Батчинг — внутренний, размер 32, configurable через `app/config.py`.

### Constraints

- Модель: `BAAI/bge-m3` через `sentence-transformers` (или `FlagEmbedding` — на выбор, зафиксировать в `pyproject.toml`).
- Размерность: 1024 (проверять через `model.get_sentence_embedding_dimension()`, падать с явной ошибкой если не совпадает с `embedding_models.dimension`).
- Normalize: `normalize_embeddings=True` (косинусная схожесть через dot product).
- Батчинг: `batch_size=32` (configurable), `max_length=512` токенов.
- GPU приоритет: если `torch.cuda.is_available()` — использовать GPU, иначе CPU с warning-логом (не падать — dev-окружение).
- Async: методы `embed_texts`/`embed_query` — async, но inference синхронный → оборачивать в `asyncio.to_thread(...)` чтобы не блокировать event loop.
- Thread safety: модель `SentenceTransformer` потокобезопасна на inference, но загрузка — нет. Использовать `asyncio.Lock` на этапе lazy-load.
- Логирование: при загрузке модели — `logger.info("Embedding model loaded", extra={"model": "bge-m3-v1", "device": "cuda"/"cpu", "dim": 1024, "load_ms": ...})`.

### References

- **ARCHITECT §5.1** — выбор модели (`bge-m3`, dim=1024).
- **ARCHITECT §5.2** — батчинг 32, GPU A10g ~800/sec, CPU ~15/sec, pseudocode обработчика батча.
- **ARCHITECT §5.5** — мультиязычность: `bge-m3` не требует отдельной модели на язык; `langdetect` для определения языка документа (делается в P-07, не здесь).
- **ROADMAP §3.2.3** — параметры: модель по умолчанию `bge-m3`, батч 32, GPU ~800/sec, CPU ~15/sec (только dev).

### Acceptance Criteria

- `EmbeddingService().embed_texts(["hello", "world"])` возвращает `np.ndarray` формы `(2, 1024)`, dtype `float32`, с L2-нормой ≈ 1.0 для каждой строки.
- `EmbeddingService().embed_query("hello world")` возвращает `np.ndarray` формы `(1024,)`, L2-норма ≈ 1.0.
- Повторный вызов `embed_texts` с теми же текстами — детерминирован (разница < 1e-6).
- Тест: запуск на CPU (через `CUDA_VISIBLE_DEVICES=""`) — работает, в логах warning.
- Тест: `embed_texts([])` — возвращает пустой массив формы `(0, 1024)`, не падает.
- Тест: батчинг 33 текстов при `batch_size=32` — обрабатывается без ошибок (внутренне разбивается на 32+1).
- `app/config.py` содержит `EmbeddingConfig` с полями `model_name`, `batch_size`, `max_length`, `device` (auto/cuda/cpu), `cache_ttl_seconds`.

### Forbidden patterns

- Не использовать `transformers` напрямую — только `sentence-transformers` (даёт normalize, mean pooling, батчинг из коробки).
- Не загружать модель при импорте модуля — только lazy при первом вызове `embed_*`.
- Не использовать `asyncio.run()` внутри async-методов — только `await asyncio.to_thread(...)`.
- Не забывать `normalize_embeddings=True` — иначе cosine в Qdrant будет давать мусор.
- Не использовать глобальный синглтон — внедрять через FastAPI Depends (с `lru_cache` на app-уровне).

### Suggested files

- `app/embedding/__init__.py`
- `app/embedding/service.py` (`class EmbeddingService`)
- `app/config.py` — добавить `EmbeddingConfig` секцию
- `tests/unit/test_embedding_service_test.py`
- `tests/conftest.py` — фикстура `embedding_service` (с маркером `@pytest.mark.slow` — моделька грузится долго)

---

## P-06 — Embedding Service: Redis-кэш (content-hash + query embedding + fast-path)

> **Компонент ROADMAP §3.2.3, §3.3** | **Артефакт**: `app/embedding/cache.py`

### Context

Два Redis-кэша (ARCHITECT §5.3): content-hash кэш для документов (TTL 30 дней) и query-embedding кэш для поисковых запросов (TTL 1 час). Fast-path по `content_hash` (TRIZ-gate ROADMAP §3.3) — повторная индексация того же контента пропускает вычисление embedding и upsert в Qdrant, экономя ~3 мс на операцию.

### Goal

Реализовать класс `EmbeddingCache` с методами:
- `get_by_content_hash(content_hash: str, model_name: str) -> np.ndarray | None`
- `set_by_content_hash(content_hash: str, model_name: str, vector: np.ndarray) -> None`
- `get_query_embedding(query: str, model_name: str) -> np.ndarray | None`
- `set_query_embedding(query: str, model_name: str, vector: np.ndarray) -> None`
- `mget_by_content_hash(items: list[tuple[str, str]]) -> list[np.ndarray | None]` (для батчинга P-05/§5.2)

И хелпер `compute_with_cache(texts, model_name, compute_fn) -> list[np.ndarray]` — оркестрирует cache-lookup → compute → cache-store.

### Constraints

- Ключи (из ARCHITECT §5.3, **точно**):
  - Content-hash: `hash:{sha256(content)}:{model_name}` → бинарный вектор (`np.ndarray.tobytes()`).
  - Query embedding: `qemb:{model_name}:{sha256(query)}` → бинарный вектор.
- TTL: content-hash = 30 дней (`2592000` секунд), query = 1 час (`3600` секунд).
- `model_name` **обязателен** в ключе (TRIZ-фикс из ARCHITECT §5.7: раньше модель не учитывалась в ключе — баг при смене модели).
- Бинарный формат: `np.ndarray.astype(np.float32).tobytes()` (1024 × 4 = 4096 байт на вектор).
- Десериализация: `np.frombuffer(data, dtype=np.float32)`.
- Redis client — `redis.asyncio.Redis` (не sync!), через connection pool.
- `mget` — использовать `redis.asyncio.Redis.mget()` (один round-trip для батча).
- При cache-miss — `compute_fn(texts)` вызывается только для тех текстов, которых нет в кэше; результат батчем пишется в кэш через `mset` + `expire` (или pipeline).
- Fast-path логика (для P-07): функция `should_skip_upsert(content_hash: str, existing_hash: str | None) -> bool` — если `existing_hash == content_hash`, возвращать `True`.

### References

- **ARCHITECT §4.3** — идемпотентность: «Embedding вычисляется детерминированно от `content_hash` → если контент не изменился, вектор не пересчитывается (кэш в Redis с ключом `hash:{sha256}:{model_name}`)».
- **ARCHITECT §4.3** — fast-path: «перед upsert воркер проверяет `content_hash` в Qdrant payload; если он совпадает — upsert пропускается (~3 мс экономии)».
- **ARCHITECT §5.3** — спецификация кэшей, TTL, формат ключей.
- **ARCHITECT §5.7** — TRIZ-анализ: «явная модельная версия в ключе кэша (раньше модель не учитывалась в ключе — баг при смене модели)».
- **ROADMAP §3.2.3** — параметры: `hash:` TTL=30 дней, `qemb:` TTL=1 час, fast-path экономит ~3 мс.
- **ROADMAP §3.3** — TRIZ-gate «Fast-path по `content_hash`».

### Acceptance Criteria

- `EmbeddingCache.get_by_content_hash(hash, model)` после `set_by_content_hash(...)` возвращает вектор, равный исходному (с точностью до float32).
- `EmbeddingCache.get_query_embedding(query, model)` после `set_query_embedding(...)` — аналогично.
- Ключи в Redis соответствуют спеки: `redis-cli KEYS "hash:*"` показывает `hash:<sha256>:bge-m3-v1`.
- TTL проверяется: `redis-cli TTL "hash:<sha256>:bge-m3-v1"` возвращает `2592000` (±5 секунд).
- `mget_by_content_hash` с 3 элементами (2 в кэше, 1 miss) — возвращает список `[vec, vec, None]` за один round-trip (проверить через `redis-cli MONITOR`).
- `compute_with_cache(texts=["a","b","c"], ...)` где "a" в кэше — вызывает `compute_fn` только с `["b","c"]`.
- Fast-path: `should_skip_upsert("abc", "abc")` → `True`, `should_skip_upsert("abc", "xyz")` → `False`, `should_skip_upsert("abc", None)` → `False`.
- Unit-тесты используют `fakeredis` или testcontainers-Redis.

### Forbidden patterns

- Не использовать `pickle` для сериализации — только `np.ndarray.tobytes()` (pickle уязвим к RCE).
- Не забывать `model_name` в ключе — это явный фикс из ARCHITECT §5.7.
- Не использовать sync `redis.Redis` — только `redis.asyncio.Redis`.
- Не вызывать `SET` для каждого вектора в батче отдельно — только `MSET` или pipeline.
- Не использовать `EXPIRE` отдельной командой после `SET` — `SET key value EX ttl` одной командой.

### Suggested files

- `app/embedding/cache.py` (`class EmbeddingCache`)
- `app/embedding/service.py` — обновить: использовать `EmbeddingCache` внутри `embed_texts`/`embed_query` (если P-05 ещё не подключил — подключить здесь).
- `app/config.py` — добавить `RedisConfig` (host, port, db, pool_size).
- `app/db/redis_client.py` — фабрика `redis.asyncio.Redis`.
- `tests/unit/test_embedding_cache_test.py`

---

## P-07 — FastAPI: `POST /index`

> **Компонент ROADMAP §3.2.4** | **Артефакт**: `app/api/routes/index.py`

### Context

`POST /index` — главный write-path системы. Реализует transactional outbox pattern (ARCHITECT §4.2): в одной PG-транзакции INSERT в `documents` + INSERT в `search_outbox`, после коммита — `XADD` в Redis Streams для embedding-воркера. Fast-path по `content_hash` (TRIZ-gate) — если контент не изменился, пропускает создание новой outbox-записи.

### Goal

Реализовать роут `POST /index` с request/response моделями из ARCHITECT §14.2 + сервисный слой `IndexingService.create(doc) -> UUID`.

### Constraints

- Request model (из ARCHITECT §14.2):
  ```python
  class IndexRequest(BaseModel):
      tenant_id: UUID
      external_ref: str
      title: str
      content: str
      language: str | None = None  # если None — langdetect
      tags: list[str] = Field(default_factory=list)
      attributes: dict = Field(default_factory=dict)
  ```
- Response model:
  ```python
  class IndexResponse(BaseModel):
      doc_id: UUID
      status: str  # "queued" (на MVP всегда "queued" — "indexed"/"throttled" в Critical)
      indexed_at: datetime
      wait_for_index_token: str | None = None  # = str(doc_id) — для будущего /index/status/{token}
  ```
- Логика (из ARCHITECT §4.2):
  1. Вычислить `content_hash = sha256(title + "\n" + content)`.
  2. Определить `language` (если None — `langdetect.detect(title + " " + content)`, fallback на tenant default из config).
  3. Открыть PG-транзакцию.
  4. **Fast-path check**: `SELECT id, content_hash FROM documents WHERE tenant_id=$1 AND external_ref=$2 AND deleted_at IS NULL`. Если найдено и `content_hash` совпадает — вернуть существующий `doc_id`, **не создавать** новую outbox-запись, response `status="queued"` (или новое значение `"no_change"` — на усмотрение, но зафиксировать в `app/api/schemas.py`).
  5. Если найдено но `content_hash` отличается — `UPDATE documents SET title=..., content=..., content_hash=..., updated_at=now() WHERE id=...`, затем `INSERT INTO search_outbox (document_id, op='upsert', content_hash=...)`.
  6. Если не найдено — `INSERT INTO documents (...)`, затем `INSERT INTO search_outbox (document_id, op='upsert', content_hash=...)`.
  7. Коммит транзакции.
  8. После коммита — `await redis.xadd("embeddings.queue", {"doc_id": str(doc_id), "op": "upsert"})`. Best-effort: если Redis недоступен — логировать warning, но не падать (reconciler подберёт по `next_retry_at`).
  9. Вернуть `IndexResponse(doc_id=..., status="queued", indexed_at=doc.created_at, wait_for_index_token=str(doc_id))`.
- `embedding_model='bge-m3-v1'`, `embedding_rev=1` — hardcoded на MVP (берётся из `get_default_model()`).
- HTTP status code: `201 Created`.
- Idempotency: повторный `POST /index` с тем же `(tenant_id, external_ref)` и тем же контентом — не создаёт дубль, возвращает тот же `doc_id`.

### References

- **ARCHITECT §2.4** — поток индексации (write path): API → PG (documents + outbox) → Redis Streams → Embedding Worker → Qdrant.
- **ARCHITECT §4.2** — transactional pattern, pseudocode `index_document`.
- **ARCHITECT §4.3** — идемпотентность + fast-path по `content_hash`.
- **ARCHITECT §5.5** — language detection (`langdetect` или `fasttext-langid`).
- **ARCHITECT §14.2** — точные request/response модели.
- **ROADMAP §3.2.4** — описание `POST /index`.
- **ROADMAP §3.3** — TRIZ-gate «Fast-path по `content_hash`».
- **ROADMAP §3.6** — риск «Потеря задачи в Redis Streams»: mitigation — reconciler не зависит от Redis.

### Acceptance Criteria

- `POST /index` с валидным телом возвращает `201` с `doc_id`, `status="queued"`, `indexed_at`.
- В БД создаются записи в `documents` и `search_outbox` (проверка SQL-запросом).
- В Redis Streams `embeddings.queue` появляется запись (проверка `XLEN embeddings.queue`).
- Повторный `POST /index` с тем же `external_ref` и тем же контентом — возвращает тот же `doc_id`, **не создаёт** новую outbox-запись (проверка `SELECT count(*) FROM search_outbox WHERE document_id=...`).
- `POST /index` с тем же `external_ref` но другим `content` — обновляет `documents` и создаёт **новую** outbox-запись.
- Если Redis недоступен (`docker-compose stop redis`) — `POST /index` всё равно возвращает `201`, в логах warning, в `search_outbox` запись создана (reconciler подберёт).
- Интеграционный тест с testcontainers (PG + Redis) покрывает все кейсы выше.

### Forbidden patterns

- Не использовать `INSERT ... ON CONFLICT DO UPDATE` напрямую — fast-path требует явной проверки `content_hash` (через SELECT), чтобы избежать создания outbox-записи при no-op.
- Не коммитить PG-транзакцию до `XADD` — `XADD` должен быть **после** коммита (иначе при rollback PG у нас остаётся «висящая» задача в Redis).
- Не использовать sync `redis.Redis` — только async.
- Не возвращать `status="indexed"` — на MVP индексация асинхронная, `indexed` появится только в Critical (через `/index/status/{token}` polling).
- Не добавлять поле `wait_for_index=true` в request — это фича Critical фазы (ARCHITECT §4.5).

### Suggested files

- `app/api/routes/__init__.py`
- `app/api/routes/index.py`
- `app/api/schemas.py` (общие request/response модели)
- `app/services/__init__.py`
- `app/services/indexing.py` (`class IndexingService`)
- `app/db/queries/documents.py` (CRUD-операции с `documents`)
- `app/db/queries/outbox.py` (CRUD с `search_outbox`)
- `tests/integration/test_index_route_test.py` (testcontainers PG + Redis)

---

## P-08 — FastAPI: `DELETE /index/{doc_id}`

> **Компонент ROADMAP §3.2.4** | **Артефакт**: обновление `app/api/routes/index.py` + `IndexingService.soft_delete`

### Context

`DELETE /index/{doc_id}` — soft-delete в Postgres (`deleted_at = now()`) + создание записи в `search_outbox` с `op='delete'`. Reconciler (P-13) подберёт эту запись и удалит точку из Qdrant. Hard-delete не делаем — нужна аудитория и возможность восстановления.

### Goal

Реализовать роут `DELETE /index/{doc_id}` (ARCHITECT §14.4) + метод `IndexingService.soft_delete(doc_id: UUID) -> None`.

### Constraints

- Логика:
  1. Открыть PG-транзакцию.
  2. `UPDATE documents SET deleted_at = now(), updated_at = now() WHERE id = $1 AND deleted_at IS NULL`.
  3. Если `rowcount == 0` — либо документ не найден (404), либо уже удалён (идемпотентный 204). Различать через `SELECT deleted_at FROM documents WHERE id=$1` — если `deleted_at IS NOT NULL` → 204 (idempotent), иначе 404.
  4. `INSERT INTO search_outbox (document_id, op='delete', content_hash=NULL)`.
  5. Коммит.
  6. `XADD embeddings.queue {"doc_id": str(doc_id), "op": "delete"}` — best-effort.
- HTTP status: `204 No Content` (из ARCHITECT §14.4).
- Response body: пустой.
- Идемпотентность: повторный `DELETE` уже удалённого документа — `204`, **не создаёт** новую outbox-запись.

### References

- **ARCHITECT §14.4** — точный код endpoint.
- **ARCHITECT §4.2** — transactional pattern (применяется тот же: PG-транзакция + post-commit `XADD`).
- **ROADMAP §3.2.4** — `DELETE /index/{doc_id}` — soft-delete в Postgres + outbox `op='delete'`.

### Acceptance Criteria

- `DELETE /index/<existing_doc_id>` → `204`, `documents.deleted_at` заполнен, в `search_outbox` запись с `op='delete'`.
- Повторный `DELETE /index/<same_doc_id>` → `204`, **новая** outbox-запись не создаётся.
- `DELETE /index/<nonexistent_uuid>` → `404` с `{"detail": "Document not found"}`.
- `DELETE /index/<invalid_uuid_string>` → `422` (FastAPI валидация UUID).
- В Redis Streams появляется запись с `op=delete` (после первого DELETE, не после повторного).
- Интеграционный тест с testcontainers покрывает все кейсы.

### Forbidden patterns

- Не делать hard-delete (`DELETE FROM documents WHERE id=...`) — нарушает аудит и восстановление.
- Не забыть `WHERE deleted_at IS NULL` в UPDATE — иначе повторный DELETE затрёт `deleted_at` (хотя значение то же, но `updated_at` сдвинется, что запутает логи).
- Не возвращать body в 204 — это нарушение HTTP-спеки.
- Не использовать `POST /index/{doc_id}/delete` — только `DELETE` метод.

### Suggested files

- `app/api/routes/index.py` — добавить `delete_document` роут
- `app/services/indexing.py` — добавить `async def soft_delete(self, doc_id: UUID) -> bool` (возвращает True если удалён, False если уже был удалён)
- `app/db/queries/documents.py` — добавить `soft_delete(doc_id) -> int` (возвращает rowcount)
- `tests/integration/test_delete_route_test.py`

---

## P-09 — FastAPI: `POST /search` — лексический канал

> **Компонент ROADMAP §3.2.4** | **Артефакт**: `app/search/lexical.py`

### Context

Лексический канал — половина гибридного поиска. Использует PostgreSQL full-text search (`ts_rank` против `tsv` GENERATED-колонки) + `pg_trgm` similarity для нечёткого match'а. Возвращает top-50 кандидатов, которые P-11 передаст в RRF fusion.

### Goal

Реализовать `async def lexical_search(query: str, tenant_id: UUID, filters: SearchFilters, k: int = 50) -> list[LexicalHit]` в `app/search/lexical.py`.

### Constraints

- `LexicalHit` pydantic-модель:
  ```python
  class LexicalHit(BaseModel):
      doc_id: UUID
      score: float
      title: str
      content_snippet: str  # первые 200 символов content
      source: Literal["lexical"] = "lexical"
  ```
- SQL (комбинированный ts_rank + trigram similarity, ARCHITECT §6.1):
  ```sql
  SELECT id, title,
         left(content, 200) AS snippet,
         (ts_rank(tsv, plainto_tsquery('simple', :q)) * 0.7
          + similarity(title || ' ' || content, :q) * 0.3) AS score
  FROM documents
  WHERE tenant_id = :tenant_id
    AND deleted_at IS NULL
    AND (:languages IS NULL OR language = ANY(:languages))
    AND (:tags IS NULL OR tags && :tags::text[])
    AND (:attrs_category IS NULL OR attributes->>'category' = :attrs_category)
    AND (:attrs_price_min IS NULL OR (attributes->>'price')::float >= :attrs_price_min)
    AND (:attrs_price_max IS NULL OR (attributes->>'price')::float <= :attrs_price_max)
    AND (:created_after IS NULL OR created_at >= :created_after)
    AND tsv @@ plainto_tsquery('simple', :q) OR similarity(title || ' ' || content, :q) > 0.3
  ORDER BY score DESC
  LIMIT :k;
  ```
  (Или эквивалент через SQLAlchemy 2.x `select()` с `func.ts_rank`, `func.plainto_tsquery`, `func.similarity`.)
- `K_lexical = 50` (из ARCHITECT §6.3, ROADMAP §3.2.4).
- Filters: `SearchFilters` из ARCHITECT §14.1 (language, tags_any, attributes, created_after).
- Tenant isolation: `tenant_id` — обязательный параметр, без него запрос не выполняется (no cross-tenant search).
- Timeout: `statement_timeout` на уровне сессии — `SET LOCAL statement_timeout = '100ms'` (через `session.execute(text("SET LOCAL ..."))` в транзакции).
- Snippet: `left(content, 200)` — простая обрезка, без highlight (highlight — Critical фаза).

### References

- **ARCHITECT §6.1** — поток запроса: лексический канал возвращает `lex_results (K=50)`.
- **ARCHITECT §6.3** — K_lexical = 50–100, на MVP фиксируем 50.
- **ARCHITECT §8.1** — pre-filter vs post-filter (на MVP — pre-filter через SQL WHERE).
- **ARCHITECT §8.3** — преобразование API-фильтров (будет в P-11 orchestrator, здесь — принимать уже `SearchFilters` pydantic-модель).
- **ARCHITECT §14.1** — `SearchFilters` модель.
- **ROADMAP §3.2.4** — K_lexical=50, BM25/`pg_trgm` similarity + filters.

### Acceptance Criteria

- `lexical_search("test query", tenant_id, filters=SearchFilters(), k=50)` возвращает `list[LexicalHit]` длиной ≤ 50.
- Результаты отсортированы по `score` убыванию.
- Фильтр `language=["ru"]` — возвращает только документы с `language='ru'`.
- Фильтр `tags_any=["news"]` — возвращает только документы, у которых `tags` пересекается с `["news"]` (оператор `&&` для массивов).
- Фильтр `attributes={"category": "AI"}` — возвращает только с `attributes->>'category' = 'AI'`.
- Tenant isolation: `tenant_id=A` не возвращает документы `tenant_id=B` (тест с двумя тенантами).
- Пустой результат (нет совпадений) — возвращает `[]`, не падает.
- `statement_timeout` срабатывает: если искусственно замедлить PG (через `pg_sleep` в тесте) — `asyncio.TimeoutError` или `asyncpg.exceptions.QueryCanceledError`.

### Forbidden patterns

- Не использовать `LIKE '%query%'` — медленно и не поддерживает морфологию. Только `ts_rank` + `pg_trgm`.
- Не забывать `WHERE deleted_at IS NULL` — soft-deleted документы не должны попадать в выдачу.
- Не использовать `ILIKE` — `pg_trgm.similarity` лучше и использует GIN-индекс.
- Не возвращать больше K=50 — это breach контракта с orchestrator'ом.
- Не использовать `SELECT *` — только нужные поля (id, title, snippet, score).

### Suggested files

- `app/search/__init__.py`
- `app/search/lexical.py` (`async def lexical_search`, `class LexicalHit`)
- `app/search/filters.py` (`SearchFilters` — если ещё нет в `app/api/schemas.py`, вынести сюда)
- `app/db/session.py` — добавить хелпер `with_statement_timeout(session, ms)`
- `tests/unit/test_lexical_search_test.py` (с testcontainers PG + seed-данными)

---

## P-10 — FastAPI: `POST /search` — векторный канал

> **Компонент ROADMAP §3.2.4** | **Артефакт**: `app/search/vector.py`

### Context

Векторный канал — вторая половина гибридного поиска. Query embedding (через P-06 кэш → P-05 модель) → Qdrant kNN + payload filter → top-50 кандидатов. Возвращает `VectorHit[]`, которые P-11 передаст в RRF fusion.

### Goal

Реализовать `async def vector_search(query: str, tenant_id: UUID, filters: SearchFilters, k: int = 50) -> list[VectorHit]` в `app/search/vector.py`.

### Constraints

- `VectorHit` pydantic-модель:
  ```python
  class VectorHit(BaseModel):
      doc_id: UUID
      score: float  # cosine similarity из Qdrant
      title: str
      content_snippet: str
      source: Literal["vector"] = "vector"
  ```
- Логика:
  1. Получить query embedding: `vec = await embedding_cache.get_query_embedding(query, model_name)`. Если cache-miss — `vec = await embedding_service.embed_query(query)`, затем `await embedding_cache.set_query_embedding(...)`.
  2. Построить Qdrant `Filter` из `SearchFilters` + `tenant_id`:
     - `must=[FieldCondition(key="tenant_id", match=MatchValue(value=str(tenant_id)))]`
     - Если `filters.language` — `should=[FieldCondition(key="language", match=MatchAny(value=filters.language))]`
     - Если `filters.tags_any` — `must` с `MatchAny` по `tags`
     - Если `filters.attributes.category` — `must` с `MatchValue` по `attributes.category`
     - Если `filters.attributes.price` — `must` с `Range` по `attributes.price`
     - Если `filters.created_after` — `must` с `Range(gte=...)` по `created_at`
     - `must` с `MatchValue(key="model_name", value="bge-m3-v1")` — фильтр по текущей модели (версионирование).
  3. `qdrant_client.search(collection_name="documents", query_vector=vec, query_filter=filter, limit=k, with_payload=True)`.
  4. Маппить результаты в `VectorHit`: для каждого — взять `payload['doc_id']`, `payload.get('title', '')` (если нет в payload — fallback: `SELECT title FROM documents WHERE id=...`, но на MVP `title` в payload не хранится — загрузить батчем из PG).
  5. Вернуть `list[VectorHit]`.
- `K_vector = 50` (из ARCHITECT §6.3, ROADMAP §3.2.4).
- Timeout: `qdrant_client` с `timeout=0.1` (100ms) — если Qdrant не ответил, поднимать `QdrantTimeoutException`.
- Tenant isolation: `tenant_id` — обязательный `must`-filter.

### References

- **ARCHITECT §6.1** — поток запроса: векторный канал возвращает `vec_results (K=50)`.
- **ARCHITECT §6.3** — K_vector = 50.
- **ARCHITECT §8.3** — преобразование API-фильтров в Qdrant conditions (полная таблица).
- **ARCHITECT §5.5** — мультиязычность: при поиске, если язык запроса определён, фильтр `language IN (query_lang, NULL)`.
- **ROADMAP §3.2.4** — K_vector=50, kNN + payload filter.

### Acceptance Criteria

- `vector_search("test query", tenant_id, filters=SearchFilters(), k=50)` возвращает `list[VectorHit]` длиной ≤ 50.
- Результаты отсортированы по `score` убыванию (Qdrant возвращает уже отсортированными).
- Фильтр `language=["ru"]` — `qdrant_client.search` вызывается с `Filter(must=[...], should=[FieldCondition(key="language", match=MatchAny(value=["ru"]))])` (проверить через mock).
- Фильтр `attributes={"category": "AI", "price": {"gte": 100, "lte": 5000}}` — `Filter` содержит `FieldCondition(key="attributes.category", match=MatchValue(value="AI"))` и `FieldCondition(key="attributes.price", range=Range(gte=100.0, lte=5000.0))`.
- Tenant isolation: `tenant_id=A` — `Filter.must` содержит `FieldCondition(key="tenant_id", match=MatchValue(value=str(A)))`.
- Query embedding cache hit — `embedding_service.embed_query` не вызывается (проверить через mock).
- Query embedding cache miss — `embed_query` вызывается, результат кэшируется.
- При Qdrant timeout (`qdrant_client.search` поднимает `TimeoutError`) — `vector_search` поднимает `QdrantTimeoutException` (кастомное исключение, обработка в P-11).

### Forbidden patterns

- Не хранить `title`/`content` в Qdrant payload на MVP (только `doc_id` + metadata). Загружать из PG батчем через `SELECT id, title, left(content, 200) FROM documents WHERE id = ANY($1)`.
- Не использовать `qdrant_client.search_batch` — на MVP один запрос за раз.
- Не забыть `model_name` filter — иначе в выдачу попадут точки от старой модели после миграции (production-ready фаза).
- Не использовать sync `qdrant_client.QdrantClient` — только `qdrant_client.AsyncQdrantClient`.
- Не возвращать `score` как есть из Qdrant (он уже cosine) — но нормализовать в `[0, 1]` через `(score + 1) / 2` если distance=Cosine (Qdrant возвращает cosine напрямую, но проверка не помешает).

### Suggested files

- `app/search/vector.py` (`async def vector_search`, `class VectorHit`)
- `app/search/qdrant_client.py` — фабрика `AsyncQdrantClient` (если ещё нет из P-04)
- `app/search/filters.py` — функция `build_qdrant_filter(tenant_id, filters, model_name) -> qdrant_client.models.Filter`
- `app/search/exceptions.py` (`class QdrantTimeoutException(Exception)`, `class QdrantUnavailableException(Exception)`)
- `tests/unit/test_vector_search_test.py` (с testcontainers Qdrant + seed-данными)

---

## P-11 — FastAPI: `POST /search` — RRF fusion + orchestrator

> **Компонент ROADMAP §3.2.4** | **Артефакт**: `app/api/routes/search.py` + `app/search/fusion.py`

### Context

Главный read-path. Оркестрирует параллельный запуск lexical (P-09) и vector (P-10) каналов, дедуплицирует по `doc_id`, применяет RRF fusion (k=60, ARCHITECT §7.1), возвращает top-20. Это «лицо» MVP — endpoint, который видит клиент.

### Goal

Реализовать роут `POST /search` (ARCHITECT §14.1) + `SearchOrchestrator` + `rrf_fuse` функцию.

### Constraints

- Request model (из ARCHITECT §14.1, но **MVP-сет** — без `rerank`, `personalize`, `diversify`, `experiment_id`):
  ```python
  class SearchRequest(BaseModel):
      query: str = Field(..., min_length=1, max_length=2048)
      tenant_id: UUID
      user_id: UUID | None = None  # принимается, но игнорируется на MVP
      filters: SearchFilters = Field(default_factory=SearchFilters)
      top_k: int = Field(20, ge=1, le=100)
      fusion: Literal["rrf"] = "rrf"  # только RRF на MVP
      explain: bool = False
      timeout_ms: int = Field(200, ge=50, le=2000)
  ```
- Response model (из ARCHITECT §14.1, MVP-сет):
  ```python
  class SearchResponse(BaseModel):
      hits: list[SearchHit]
      facets: dict[str, list[FacetBucket]] = {}  # пустой на MVP, заполняется в Critical
      total_lexical: int
      total_vector: int
      latency_ms: int
      degraded: bool = False  # всегда False на MVP — degraded mode в Critical
  ```
- `SearchHit`: `doc_id, score, title, snippet, attributes, debug: dict | None`.
- Логика:
  1. `deadline = time.monotonic() + req.timeout_ms / 1000`.
  2. Параллельно (`asyncio.gather`): `lexical_search(...)` и `vector_search(...)`.
     - Обернуть каждый в `asyncio.wait_for(..., timeout=remaining_to_deadline)`.
     - При `TimeoutError` в одном канале — возвращать то, что успели из другого (partial result).
     - При `QdrantUnavailableException` — `total_vector=0`, `hits` только из lexical, `degraded=True`.
  3. Дедупликация по `doc_id` (если документ есть в обоих каналах — оставляем обе записи для RRF, дедупликация по `doc_id` происходит в fusion).
  4. RRF fusion (k=60, формула из ARCHITECT §7.1):
     ```python
     def rrf_fuse(lex: list[LexicalHit], vec: list[VectorHit], k: int = 60) -> list[tuple[UUID, float]]:
         scores: dict[UUID, float] = defaultdict(float)
         for rank, hit in enumerate(lex, start=1):
             scores[hit.doc_id] += 1 / (k + rank)
         for rank, hit in enumerate(vec, start=1):
             scores[hit.doc_id] += 1 / (k + rank)
         return sorted(scores.items(), key=lambda x: -x[1])
     ```
  5. Взять top `req.top_k` (default 20).
  6. Для каждого `doc_id` в топе — собрать `SearchHit`: `title`/`snippet`/`attributes` из hits (lexical или vector, любой — они должны совпадать, но fallback на PG-запрос если ни в одном нет).
  7. `latency_ms = int((time.monotonic() - start) * 1000)`.
  8. Вернуть `SearchResponse`.
- `explain=True` → в `debug` поле каждого hit: `{"lexical_rank": ..., "vector_rank": ..., "lexical_score": ..., "vector_score": ..., "rrf_score": ...}`.

### References

- **ARCHITECT §2.3** — поток запроса (read path): API → Redis (query emb cache) → параллельно PG + Qdrant → dedup → fuse → response.
- **ARCHITECT §6.1** — этапы обработки запроса, mermaid-диаграмма.
- **ARCHITECT §7.1** — RRF формула, default k=60, pseudocode `rrf_fuse`.
- **ARCHITECT §14.1** — точные request/response модели (включая `SearchHit`, `FacetBucket`, `SearchResponse`).
- **ROADMAP §3.2.4** — описание `POST /search`, параметры RRF (k=60), top-20.

### Acceptance Criteria

- `POST /search` с query="test" возвращает `200` с `hits` длиной ≤ `top_k`.
- `hits` отсортированы по `score` убыванию.
- `total_lexical` = количество результатов от lexical канала (до fusion), `total_vector` — от vector.
- Если документ есть в обоих каналах — он один раз в `hits` (дедупликация работает), его `score` = RRF-сумма.
- `explain=True` → каждый hit содержит `debug` с `lexical_rank`, `vector_rank`, `rrf_score`.
- При искусственном таймауте Qdrant (`timeout_ms=10`) — `degraded=True`, `hits` только из lexical, `total_vector=0`.
- При искусственном таймауте PG (`SET statement_timeout=1ms`) — `degraded=True`, `hits` только из vector, `total_lexical=0`.
- При таймауте обоих каналов — `500` с `{"detail": "Search timeout"}`.
- `latency_ms` в ответе ≤ `timeout_ms` из запроса (с маленьким запасом).
- Интеграционный E2E-тест: `POST /index` → ждём 30с (reconciler отработает) → `POST /search` → документ в выдаче.

### Forbidden patterns

- Не запускать каналы последовательно (`await lex; await vec`) — только `asyncio.gather`. Это убьёт latency.
- Не использовать `weighted` fusion на MVP — только `rrf`. Weighted → Critical (ARCHITECT §7.2).
- Не добавлять `rerank`, `personalize`, `diversify` — Critical/Production-Ready.
- Не возвращать `facets` заполненными — на MVP пустой dict (Critical добавит, ARCHITECT §8.4).
- Не использовать `time.time()` — только `time.monotonic()` (не подвержен NTP-скачкам).

### Suggested files

- `app/api/routes/search.py`
- `app/search/fusion.py` (`def rrf_fuse`, `def weighted_fuse` — последний закомментирован/заглушён для Critical)
- `app/search/orchestrator.py` (`class SearchOrchestrator`)
- `app/api/schemas.py` — `SearchRequest`, `SearchResponse`, `SearchHit`, `SearchFilters`, `FacetBucket`
- `tests/integration/test_search_route_test.py`

---

## P-12 — FastAPI: Health-эндпоинты + `/metrics`

> **Компонент ROADMAP §3.2.4, §3.2.6** | **Артефакт**: `app/api/routes/health.py` + Prometheus-интеграция

### Context

Health-эндпоинты нужны для k8s/Docker readiness probes. `/metrics` — для Prometheus scrape. На MVP метрики минимальны (full set в P-15), здесь — каркас + readiness check, который проверяет связность с PG, Qdrant и lag outbox.

### Goal

Реализовать `GET /health/live`, `GET /health/ready`, `GET /metrics` + подключить `prometheus-fastapi-instrumentator`.

### Constraints

- `GET /health/live` → `200 {"status": "alive"}` — процесс жив, без проверок зависимостей.
- `GET /health/ready` → `200 {"status": "ready", "checks": {...}}` или `503 {"status": "not_ready", "checks": {...}}`.
  - Checks:
    - `postgres`: `SELECT 1` — `ok`/`fail`.
    - `qdrant`: `GET /collections/documents` через Qdrant REST API или `qdrant_client.get_collection_info` — `ok`/`fail`.
    - `redis`: `PING` — `ok`/`fail`.
    - `outbox_lag`: `SELECT count(*) FROM search_outbox WHERE status IN ('pending','failed') AND next_retry_at <= now()` — `ok` если `count < 1000`, `degraded` если `1000 ≤ count < 10000`, `fail` если `count ≥ 10000`.
  - Если хотя бы один check `fail` — HTTP `503`.
  - `outbox_lag=degraded` не делает overall статус `fail` — только `degraded` в response.
- `GET /metrics` → Prometheus exposition format (text/plain, version=0.0.4).
  - Подключить `prometheus-fastapi-instrumentator` (авто-метрики `http_requests_total`, `http_request_duration_seconds`).
  - Кастомные метрики (реализовать в P-15, здесь только каркас — gauge `index_lag_seconds` initialised with 0).
- Эндпоинты `/health/*` и `/metrics` **не**instrumented прометхеусом (исключить через `should_ignore_exception` или `excluded_handlers`).

### References

- **ARCHITECT §14.5** — спецификация health-эндпоинтов.
- **ARCHITECT §11.1** — метрики (полный список будет в P-15, здесь каркас).
- **ROADMAP §3.2.4** — описание health-эндпоинтов.
- **ROADMAP §3.2.6** — observability baseline.
- **ROADMAP §3.4 (DoD)** — «Prometheus-метрики публикуются».

### Acceptance Criteria

- `GET /health/live` всегда возвращает `200` (если процесс жив).
- `GET /health/ready` при работающих PG+Qdrant+Redis — `200`, `checks.postgres=ok`, `checks.qdrant=ok`, `checks.redis=ok`, `checks.outbox_lag=ok`.
- `GET /health/ready` при остановленном Qdrant — `503`, `checks.qdrant=fail`.
- `GET /health/ready` при `outbox` с 5000 pending — `200` с `checks.outbox_lag=degraded`.
- `GET /health/ready` при `outbox` с 15000 pending — `503` с `checks.outbox_lag=fail`.
- `GET /metrics` возвращает text/plain с метриками `http_requests_total`, `http_request_duration_seconds`, `index_lag_seconds`.
- `curl http://localhost:8000/health/ready | jq .checks` — структурированный JSON.

### Forbidden patterns

- Не делать heavy-запросы в `/health/live` — он должен отвечать за <1ms.
- Не кэшировать результат `/health/ready` дольше 1 секунды — статусы меняются.
- Не использовать `select 1 from documents limit 1` — `SELECT 1` дешевле и не зависит от таблиц.
- Не публиковать метрики health-эндпоинтов в Prometheus — они засоряют дашборд.
- Не возвращать `503` без body — клиенту нужна структура `checks` для понимания, что именно упало.

### Suggested files

- `app/api/routes/health.py`
- `app/observability/__init__.py`
- `app/observability/health.py` (`class HealthChecker`, `check_postgres`, `check_qdrant`, `check_redis`, `check_outbox_lag`)
- `app/main.py` — подключить `prometheus-fastapi-instrumentator` в `create_app()`
- `tests/integration/test_health_route_test.py`

---

## P-13 — Reconciler-воркер

> **Компонент ROADMAP §3.2.5** | **Артефакт**: `app/reconciler/worker.py` + CLI entry point

### Context

Reconciler — safety net для dual-write. Раз в 30 секунд сканирует `search_outbox` на pending/failed записи, обрабатывает их (embed → upsert в Qdrant → mark done), при ошибке — backoff, после 20 попыток — `dead` (TRIZ-gate). Без него система не восстановится после сбоя Qdrant.

### Goal

Реализовать `class ReconcilerWorker` с методом `run_forever()` + CLI entry point `python -m app.reconciler.worker`.

### Constraints

- Параметры (из ARCHITECT §17.2 `config.yaml` → `reconciler`):
  - `poll_interval_seconds = 30`
  - `batch_size = 500`
  - `max_attempts = 20`
  - `base_backoff_seconds = 10`
  - `max_backoff_seconds = 3600`
  - `digest_interval_minutes = 60` (для P-14, здесь — только параметр)
- Основной цикл (ARCHITECT §4.4):
  ```python
  while True:
      batch = await fetch_batch()  # SELECT ... LIMIT 500 FOR UPDATE SKIP LOCKED
      if not batch:
          await asyncio.sleep(poll_interval)
          continue
      await process_batch(batch)
  ```
- `fetch_batch()`:
  ```sql
  SELECT id, document_id, op, attempts, content_hash
  FROM search_outbox
  WHERE status IN ('pending','failed') AND next_retry_at <= now()
  ORDER BY next_retry_at
  LIMIT 500
  FOR UPDATE SKIP LOCKED;
  ```
  В одной транзакции — чтобы `FOR UPDATE SKIP LOCKED` работал. Транзакция держится до конца обработки батча (или использовать `FOR UPDATE` в одной транзакции, потом update статусов в другой).
- `process_batch(batch)`:
  1. Для каждой записи: `UPDATE search_outbox SET status='in_progress', attempts=attempts+1, updated_at=now() WHERE id=...`.
  2. Коммит (чтобы другие воркеры видели in_progress).
  3. Загрузить `document` из PG (`SELECT * FROM documents WHERE id=...`).
  4. Если `op='delete'` — `qdrant_client.delete(collection_name='documents', points_selector=PointIdsList(points=[str(doc_id)]))`.
  5. Если `op='upsert'`:
     a. Fast-path: `existing = qdrant_client.retrieve(collection_name='documents', ids=[str(doc_id)], with_payload=True)`. Если `existing[0].payload['content_hash'] == record.content_hash` — skip upsert, mark done.
     b. Cache lookup: `vec = await embedding_cache.get_by_content_hash(record.content_hash, model_name)`. Если miss — `vec = await embedding_service.embed_texts([document.content])[0]`, `await embedding_cache.set_by_content_hash(...)`.
     c. `qdrant_client.upsert(collection_name='documents', points=[PointStruct(id=str(doc_id), vector=vec, payload=QdrantPayload(...).model_dump())])`.
  6. При успехе: `UPDATE search_outbox SET status='done', updated_at=now() WHERE id=...`.
  7. При ошибке (исключение): `UPDATE search_outbox SET status='failed', last_error=str(e), next_retry_at=now() + min(2^attempts * 10s, 1h), updated_at=now() WHERE id=...`.
  8. Если `attempts >= max_attempts` (20): `UPDATE search_outbox SET status='dead', updated_at=now() WHERE id=...` + лог `logger.error("Dead letter", extra={...})` + метрика `dead_letter_count.inc()`.
- Qdrant payload — строго через `QdrantPayload` из P-04 (schema drift prevention).
- Граcful shutdown: `SIGTERM` → завершить текущий батч, не начинать новый, выйти.
- Параллелизм внутри батча: `asyncio.gather` для embed+upsert (с semaphore 10, чтобы не перегрузить Qdrant).

### References

- **ARCHITECT §4.4** — алгоритм reconciler, SQL-запрос, retry-логика, dead-letter.
- **ARCHITECT §4.3** — fast-path по `content_hash` (используется в step 5a).
- **ARCHITECT §4.5** — гарантии согласованности (Final consistency SLO, Dead-letter safety).
- **ARCHITECT §5.2** — батчинг 32 для embedding (применяется при последовательной обработке нескольких outbox-записей — собрать тексты, embed батчем, потом upsert).
- **ARCHITECT §17.2** — `config.yaml::reconciler` параметры.
- **ROADMAP §3.2.5** — описание reconciler, интервал 30с, batch 500, retry exponential, dead при attempts>20.
- **ROADMAP §3.3** — TRIZ-gate «`dead`-состояние в outbox».
- **ROADMAP §3.4 (DoD)** — «Reconciler восстанавливает рассинхрон при принудительном падении Qdrant».

### Acceptance Criteria

- `python -m app.reconciler.worker` стартует, логирует «Reconciler started».
- При пустом outbox — `sleep(30)`, не делает SQL `SELECT` каждые 30с (оптимизация: `SELECT EXISTS` перед fetch_batch).
- При вставке в `search_outbox` записи `op='upsert'` → воркер за < 35 секунд: вычисляет embedding, upsert в Qdrant, ставит `status='done'`.
- При вставке `op='delete'` → воркер удаляет точку из Qdrant, ставит `status='done'`.
- При ошибке Qdrant (kill контейнер) → `status='failed'`, `next_retry_at` = `now() + 2^attempts * 10s`.
- После 20 неудачных попыток → `status='dead'`, `last_error` заполнен, метрика `dead_letter_count` инкрементирована.
- `SIGTERM` → воркер завершает текущий батч (не более 60 секунд), затем выходит с кодом 0.
- Fast-path: вставка outbox-записи с тем же `content_hash`, что уже в Qdrant → upsert пропускается, `status='done'` сразу.
- E2E-тест (DoD §3.4): `POST /index` → kill Qdrant → подождать 60с → запустить Qdrant → за < 60с `outbox` разгребается, документ находится через `POST /search`.

### Forbidden patterns

- Не использовать `SELECT *` без `FOR UPDATE SKIP LOCKED` — будут race conditions между воркерами.
- Не забывать `WHERE status IN ('pending','failed')` — `in_progress` и `done` не должны попадать в батч.
- Не использовать `time.sleep` — только `asyncio.sleep`.
- Не игнорировать `SIGTERM` — в k8s это приведёт к SIGKILL через 30с и потере in-progress батча.
- Не вызывать `qdrant_client.upsert` синхронно — только async client.
- Не забывать fast-path — без него reindex будет пересчитывать все embeddings.

### Suggested files

- `app/reconciler/__init__.py`
- `app/reconciler/worker.py` (`class ReconcilerWorker`, `__main__` блок)
- `app/reconciler/config.py` (или расширить `app/config.py` секцией `ReconcilerConfig`)
- `app/db/queries/outbox.py` — `fetch_batch()`, `mark_in_progress()`, `mark_done()`, `mark_failed()`, `mark_dead()`
- `app/services/qdrant.py` — `upsert_point(doc_id, vector, payload)`, `delete_point(doc_id)`, `get_point_content_hash(doc_id) -> str | None`
- `tests/integration/test_reconciler_worker_test.py` (testcontainers PG + Qdrant + Redis)

---

## P-14 — Digest-воркер для dead-записей

> **Компонент ROADMAP §3.2.5, §3.3** | **Артефакт**: `app/reconciler/digest.py` + миграция `004_dead_digest`

### Context

Когда `search_outbox` накапливает dead-записи (после 20 неудачных попыток), разбирать их построчно — ад (10k+ строк). Digest-воркер (TRIZ-gate ROADMAP §3.3) раз в час агрегирует dead по `(tenant_id, model_name, error_type)`, давая «карту здоровья» из ~10–100 строк.

### Goal

1. Создать миграцию `004_dead_digest` с таблицей `search_outbox_dead_digest`.
2. Реализовать `class DigestWorker` с `run_forever()` + CLI `python -m app.reconciler.digest`.

### Constraints

- Миграция `004_dead_digest`:
  ```sql
  CREATE TABLE search_outbox_dead_digest (
      id BIGSERIAL PRIMARY KEY,
      tenant_id UUID NOT NULL,
      model_name TEXT NOT NULL,
      error_type TEXT NOT NULL,  -- категория: 'qdrant_unavailable', 'embedding_failed', 'payload_invalid', 'other'
      count BIGINT NOT NULL,
      first_seen_at TIMESTAMPTZ NOT NULL,
      last_seen_at TIMESTAMPTZ NOT NULL,
      sample_doc_ids UUID[] NOT NULL DEFAULT '{}',  -- до 10 примеров
      sample_errors TEXT[] NOT NULL DEFAULT '{}',   -- до 10 примеров last_error
      UNIQUE (tenant_id, model_name, error_type)
  );
  CREATE INDEX search_outbox_dead_digest_tenant_idx ON search_outbox_dead_digest (tenant_id);
  CREATE INDEX search_outbox_dead_digest_last_seen_idx ON search_outbox_dead_digest (last_seen_at DESC);
  ```
- Digest-воркер:
  - Интервал: 60 минут (configurable).
  - SQL агрегации:
    ```sql
    INSERT INTO search_outbox_dead_digest (tenant_id, model_name, error_type, count, first_seen_at, last_seen_at, sample_doc_ids, sample_errors)
    SELECT
        d.tenant_id,
        d.embedding_model AS model_name,
        CASE
            WHEN o.last_error ILIKE '%qdrant%' OR o.last_error ILIKE '%timeout%' THEN 'qdrant_unavailable'
            WHEN o.last_error ILIKE '%embedding%' OR o.last_error ILIKE '%model%' THEN 'embedding_failed'
            WHEN o.last_error ILIKE '%payload%' OR o.last_error ILIKE '%validation%' THEN 'payload_invalid'
            ELSE 'other'
        END AS error_type,
        count(*) AS count,
        min(o.updated_at) AS first_seen_at,
        max(o.updated_at) AS last_seen_at,
        array_agg(d.id ORDER BY o.updated_at DESC LIMIT 10) AS sample_doc_ids,
        array_agg(o.last_error ORDER BY o.updated_at DESC LIMIT 10) AS sample_errors
    FROM search_outbox o
    JOIN documents d ON d.id = o.document_id
    WHERE o.status = 'dead'
      AND o.updated_at > :since  -- только новые с последнего запуска
    GROUP BY d.tenant_id, d.embedding_model, error_type
    ON CONFLICT (tenant_id, model_name, error_type)
    DO UPDATE SET
        count = search_outbox_dead_digest.count + EXCLUDED.count,
        last_seen_at = EXCLUDED.last_seen_at,
        sample_doc_ids = array_cat(search_outbox_dead_digest.sample_doc_ids, EXCLUDED.sample_doc_ids)[1:10],
        sample_errors = array_cat(search_outbox_dead_digest.sample_errors, EXCLUDED.sample_errors)[1:10];
    ```
  - После агрегации — лог: `logger.info("Dead letter digest updated", extra={"groups": N, "total_dead": M})`.
- Error type classification — простой ILIKE, не regex (на MVP достаточно).
- `since` параметр — `now() - interval '2 hours'` (окно с запасом, чтобы не пропустить ничего при сбое воркера).

### References

- **ARCHITECT §4.4** — «reconciler-digest-воркер раз в час агрегирует `dead`-записи в `search_outbox_dead_digest` (по tenant_id, model_name, error_type)».
- **ARCHITECT §4.7 (TRIZ)** — Принцип 22 «Обратить вред в пользу»: «`dead`-записи не "мусор", а источник диагностической информации; digest-таблица превращает их в инструмент мониторинга целостности данных».
- **ROADMAP §3.2.5** — digest-воркер: раз в час, агрегация по `(tenant_id, model_name, error_type)`.
- **ROADMAP §3.3** — TRIZ-gate «Digest-агрегатор dead-записей».
- **ROADMAP §3.4 (DoD)** — «`dead`-записи появляются в `search_outbox_dead_digest` (проверка инъекцией заведомо сломанного документа)».

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу `search_outbox_dead_digest` с индексами.
- После инъекции сломанного документа (например, `embedding_model='nonexistent-model'`) и 20 неудачных попыток reconciler'а — `search_outbox` содержит запись `status='dead'`.
- После запуска `python -m app.reconciler.digest` — `search_outbox_dead_digest` содержит строку с `error_type='embedding_failed'`, `count=1`, `sample_doc_ids` содержит UUID сломанного документа.
- Повторный запуск digest-воркера после новой dead-записи с тем же `(tenant_id, model_name, error_type)` — `count` инкрементируется, `last_seen_at` обновляется, `sample_doc_ids` содержит оба UUID.
- Digest-воркер идемпотентен: повторный запуск без новых dead-записей — не меняет данные.
- `SIGTERM` → graceful shutdown.

### Forbidden patterns

- Не удалять dead-записи из `search_outbox` после агрегации — они нужны для аудита (только `search_events` партиции можно дропать, не outbox).
- Не использовать regex для error_type — ILIKE быстрее и проще.
- Не агрегировать все dead-записи каждый раз — только `updated_at > :since` (иначе с ростом outbox воркер будет тормозить).
- Не забывать `ON CONFLICT DO UPDATE` — иначе будут дубликаты.
- Не хранить больше 10 `sample_doc_ids`/`sample_errors` — иначе массивы раздуваются.

### Suggested files

- `alembic/versions/004_dead_digest.py`
- `app/reconciler/digest.py` (`class DigestWorker`, `__main__`)
- `app/db/queries/digest.py` (`aggregate_dead_letters(since: datetime) -> int`)
- `tests/integration/test_digest_worker_test.py`

---

## P-15 — Observability baseline: метрики + structured logs

> **Компонент ROADMAP §3.2.6** | **Артефакт**: `app/observability/metrics.py` + `app/observability/logging.py`

### Context

MVP-observability достаточна для отладки (не для управления качеством — это Critical). 5 Prometheus-метрик + structured JSON-логи с `request_id`/`tenant_id`/`trace_id` (заглушка для будущего OTel). Без этого невозможно понять, что происходит в проде.

### Goal

1. Реализовать все 5 метрик из ROADMAP §3.2.6 в `app/observability/metrics.py`.
2. Реализовать structured JSON-logger с контекстными полями в `app/observability/logging.py`.
3. Интегрировать метрики в `IndexingService` (P-07), `SearchOrchestrator` (P-11), `ReconcilerWorker` (P-13).

### Constraints

- Метрики (из ROADMAP §3.2.6, ARCHITECT §11.1):
  1. `search_latency_ms` — Histogram, labels: `tenant_id`, `fusion`. Заполняется в `SearchOrchestrator.search()` после каждого запроса.
     - Buckets: `[10, 25, 50, 100, 150, 200, 300, 500, 1000, 2000]` (ms).
  2. `index_lag_seconds` — Gauge. Заполняется в `ReconcilerWorker` раз в poll: `SELECT EXTRACT(EPOCH FROM now() - min(created_at)) FROM search_outbox WHERE status IN ('pending','failed')`. Если outbox пуст — `0`.
  3. `qdrant_upsert_errors_total` — Counter, labels: `error_type` (`timeout`, `unavailable`, `validation`, `other`). Инкрементируется в `ReconcilerWorker.process_batch()` при ошибке upsert.
  4. `embedding_cache_hit_rate` — Gauge. Заполняется в `EmbeddingCache`: `hits / (hits + misses)` за скользящее окно (или простой счётчик `embedding_cache_hits_total` / `embedding_cache_requests_total`, а gauge считать в Prometheus через `rate()`).
     - На MVP: два counter'а `embedding_cache_hits_total` и `embedding_cache_requests_total`, gauge считается в Grafana.
  5. `dead_letter_count` — Gauge. Заполняется в `ReconcilerWorker` при переводе записи в `dead`: `SELECT count(*) FROM search_outbox WHERE status='dead'` (или инкрементальный counter `dead_letters_total` + gauge через `search_outbox_dead_digest`).
     - На MVP: counter `dead_letters_total` + периодический gauge `dead_letter_count` (раз в минуту из `ReconcilerWorker`).
- Structured JSON-логи:
  - Формат: `{"timestamp": "...", "level": "INFO", "logger": "...", "message": "...", "request_id": "...", "tenant_id": "...", "trace_id": "...", "extra": {...}}`.
  - Использовать `structlog` или `python-json-logger` (зафиксировать в `pyproject.toml`).
  - `request_id` — генерируется в middleware для каждого HTTP-запроса (UUID4), прокидывается во все логи через `contextvars`.
  - `tenant_id` — извлекается из request body (для `/index`, `/search`) или из path (`/index/{doc_id}`).
  - `trace_id` — заглушка: тот же UUID, что `request_id` (будет заменён на OTel trace_id в Production-Ready).
  - Лог-уровень configurable через `LOG_LEVEL` env (default `INFO`).
- Middleware: `app/api/middleware/request_id.py` — генерирует `request_id`, кладёт в `contextvars.ContextVar`, добавляет header `X-Request-ID` в response.

### References

- **ARCHITECT §11.1** — метрики (полный список, в т.ч. future-фазы).
- **ARCHITECT §11.4** — логирование запросов: `request_id`, `tenant_id`, `trace_id`.
- **ARCHITECT §11.2** — distributed tracing (OTel — это Production-Ready, здесь только заглушка `trace_id`).
- **ROADMAP §3.2.6** — exact список 5 метрик + structured JSON-логи.
- **ROADMAP §3.4 (DoD)** — «Prometheus-метрики публикуются; Grafana-дашборд отображает latency, throughput, lag, dead-letter count».

### Acceptance Criteria

- `GET /metrics` содержит все 5 метрик (с `_total`/`_bucket`/`_sum`/`_count` суффиксами для counter/histogram).
- После `POST /search` — `search_latency_ms_count{tenant_id=...,fusion="rrf"}` инкрементирован.
- После `POST /index` + 35с ожидания — `index_lag_seconds` updated (либо `0` если outbox пуст, либо > 0).
- После ошибки Qdrant в reconciler — `qdrant_upsert_errors_total{error_type="timeout"}` инкрементирован.
- После 20 неудачных попыток — `dead_letters_total` инкрементирован, `dead_letter_count` gauge updated.
- Логи в stdout — валидный JSON (проверка `jq`).
- Каждый лог содержит `request_id` (для HTTP-запросов) или пустую строку (для воркеров).
- `X-Request-ID` header присутствует в response.
- Unit-тест: `EmbeddingCache` инкрементирует `embedding_cache_hits_total` / `embedding_cache_requests_total`.

### Forbidden patterns

- Не использовать `print()` — только `logger.info/debug/warning/error`.
- Не использовать `logging.getLogger(__name__)` напрямую — обернуть в `structlog.get_logger(__name__)` (или эквивалент).
- Не хардкодить `request_id` — только через `contextvars`.
- Не публиковать sensitive данные (content, title) в логах — только `doc_id`, `tenant_id`, `content_hash`.
- Не использовать `prometheus_client.Counter` напрямую — обернуть в `app/observability/metrics.py` singleton, чтобы метрики регистрировались один раз.

### Suggested files

- `app/observability/__init__.py`
- `app/observability/metrics.py` (singleton `METRICS` со всеми метриками)
- `app/observability/logging.py` (`setup_logging(level: str)`, `get_logger(name: str)`)
- `app/api/middleware/__init__.py`
- `app/api/middleware/request_id.py`
- `app/main.py` — подключить middleware и `setup_logging()` в `create_app()`
- `app/services/indexing.py` — добавить `logger.info("Document indexed", extra={...})` и метрики
- `app/search/orchestrator.py` — добавить `search_latency_ms.observe(...)`
- `app/reconciler/worker.py` — добавить `index_lag_seconds.set(...)`, `qdrant_upsert_errors_total.inc(...)`, `dead_letters_total.inc()`
- `tests/unit/test_metrics_test.py`
- `tests/unit/test_logging_test.py`

---

## P-16 — Grafana dashboards

> **Компонент ROADMAP §3.2.6** | **Artefact**: `grafana/provisioning/*.json`

### Context

Метрики без визуализации — мёртвый груз. Grafana-дашборды дают команде быстрый взгляд на здоровье MVP. На MVP — 4 базовых дашборда: throughput, latency, error rate, outbox lag/dead-letter.

### Goal

Создать 4 Grafana JSON-дашборда в `grafana/provisioning/dashboards/` + `grafana/provisioning/datasources/prometheus.yml`.

### Constraints

- DataSource: Prometheus (URL `http://prometheus:9090` — через docker network, или `http://localhost:9090` для dev).
- Dashboards:
  1. **MVP — Overview** (`mvp-overview.json`):
     - Panel 1: Throughput (`rate(http_requests_total{handler=~"/index|/search"}[1m])`), разделение по handler.
     - Panel 2: Error rate (`rate(http_requests_total{status=~"5.."}[1m]) / rate(http_requests_total[1m])`).
     - Panel 3: Outbox lag (`index_lag_seconds`).
     - Panel 4: Dead letters (`dead_letter_count` + `rate(dead_letters_total[5m])`).
  2. **MVP — Search Latency** (`mvp-search-latency.json`):
     - Panel 1: p50/p95/p99 (`histogram_quantile(0.5, rate(search_latency_ms_bucket[5m]))` и т.д.).
     - Panel 2: Latency heatmap (`search_latency_ms_bucket`).
     - Panel 3: Latency by tenant (`histogram_quantile(0.99, rate(search_latency_ms_bucket{tenant_id="..."}[5m]))`).
     - Panel 4: Embedding cache hit rate (`rate(embedding_cache_hits_total[5m]) / rate(embedding_cache_requests_total[5m])`).
  3. **MVP — Indexing Health** (`mvp-indexing-health.json`):
     - Panel 1: Index throughput (`rate(http_requests_total{handler="/index",status="201"}[1m])`).
     - Panel 2: Outbox queue depth (`search_outbox_pending_count` — нужен gauge метрика, добавить в P-15 если ещё нет: `outbox_pending_count` gauge, обновлять в reconciler).
     - Panel 3: Qdrant upsert errors (`rate(qdrant_upsert_errors_total[5m]) by (error_type)`).
     - Panel 4: Reconciler batch size (`reconciler_batch_size` — gauge, добавить в P-15 если нет).
  4. **MVP — Dead Letters** (`mvp-dead-letters.json`):
     - Panel 1: Dead count over time (`dead_letter_count`).
     - Panel 2: Dead by tenant (запрос к PG через `grafana-postgres-datasource` — `SELECT tenant_id, model_name, error_type, count, last_seen_at FROM search_outbox_dead_digest ORDER BY last_seen_at DESC LIMIT 100`).
     - Panel 3: Sample errors (та же таблица, колонка `sample_errors`).
     - Panel 4: Alert: dead count > 0 (Grafana alert rule).
- Provisioning:
  - `grafana/provisioning/datasources/datasources.yml` — Prometheus + Postgres datasource (Postgres — для dead-letter дашборда).
  - `grafana/provisioning/dashboards/dashboards.yml` — указывает на `grafana/provisioning/dashboards/*.json`.
- `docker-compose.yml` — добавить сервис `grafana` (образ `grafana/grafana:latest`, port 3000, volume на `./grafana/provisioning`).

### References

- **ARCHITECT §11.1** — список метрик.
- **ROADMAP §3.2.6** — «базовые Grafana-дашборды: throughput, latency, error rate, outbox lag».
- **ROADMAP §3.4 (DoD)** — «Grafana-дашборд отображает latency, throughput, lag, dead-letter count».

### Acceptance Criteria

- `docker-compose up` поднимает Grafana на `http://localhost:3000` (admin/admin).
- Все 4 дашборда автоматически появляются в Grafana (через provisioning, без ручного импорта).
- DataSource Prometheus автоматически сконфигурирован, статус «Connected» в Grafana UI.
- DataSource Postgres автоматически сконфигурован, статус «Connected».
- На дашборде «MVP — Overview» после нескольких `POST /index` + `POST /search` видны ненулевые значения throughput и latency.
- На «MVP — Dead Letters» после инъекции сломанного документа и 20 попыток — видна строка в таблице dead-digest.
- Alert «dead count > 0» переходит в `Pending` → `Firing` при появлении dead-записи.

### Forbidden patterns

- Не хардкодить URL Prometheus в JSON-дашбордах — использовать `${DS_PROMETHEUS}` variable.
- Не публиковать admin-пароль в `docker-compose.yml` — через env `GF_SECURITY_ADMIN_PASSWORD`.
- Не использовать deprecated Grafana JSON-схему (v1) — только актуальную (v2 для новых Grafana).
- Не забывать `version` поле в JSON (для отслеживания изменений).
- Не добавлять дашборды из Critical/Production-Ready фазы (reranker latency, A/B, OTel traces).

### Suggested files

- `grafana/provisioning/datasources/datasources.yml`
- `grafana/provisioning/dashboards/dashboards.yml`
- `grafana/provisioning/dashboards/mvp-overview.json`
- `grafana/provisioning/dashboards/mvp-search-latency.json`
- `grafana/provisioning/dashboards/mvp-indexing-health.json`
- `grafana/provisioning/dashboards/mvp-dead-letters.json`
- `docker-compose.yml` — добавить сервис `grafana`
- `app/observability/metrics.py` — при необходимости добавить `outbox_pending_count` gauge и `reconciler_batch_size` gauge

---

## P-17 — E2E-тесты и приёмка DoD

> **Компонент ROADMAP §3.4, §3.5** | **Artefact**: `tests/e2e/test_mvp_dod.py` + `scripts/run_dod_check.sh`

### Context

Финальный промпт фазы MVP. Цель — формально проверить Definition of Done из ROADMAP §3.4 и NFR-таргеты из §3.5. Если все 6 DoD-критериев зелёные — MVP готов к переходу в Critical (ROADMAP §9.1 exit criteria).

### Goal

Реализовать E2E-test suite `tests/e2e/test_mvp_dod.py`, покрывающий все 6 пунктов DoD из ROADMAP §3.4 + NFR-замеры через `scripts/run_dod_check.sh`.

### Constraints

- **DoD 1**: «`POST /index` создаёт документ в Postgres и (в течение SLO 30 сек) — в Qdrant».
  - Тест: `POST /index` → poll `GET /search` раз в 2 секунды → документ должен появиться в выдаче за ≤ 30 секунд.
- **DoD 2**: «`POST /search` возвращает RRF-fused результаты из двух каналов; результаты дедуплицированы по `doc_id`».
  - Тест: вставить документ, который матчится и лексически, и векторно → проверить, что он **один раз** в `hits`, его `debug.rrf_score` (при `explain=true`) > 0.
  - Тест: вставить документ, который матчится только лексически (опечатка в редком термине, не ловится embedding'ом) → он в `hits`, `debug.vector_rank` = `null`.
  - Тест: вставить документ, который матчится только векторно (парафраз) → он в `hits`, `debug.lexical_rank` = `null`.
- **DoD 3**: «Reconciler восстанавливает рассинхрон при принудительном падении Qdrant».
  - Тест: `POST /index` → `docker-compose stop qdrant` → подождать 60с (reconciler копит pending) → `docker-compose start qdrant` → за ≤ 60с outbox разгребается, документ в выдаче.
- **DoD 4**: «Prometheus-метрики публикуются; Grafana-дашборд отображает latency, throughput, lag, dead-letter count».
  - Тест: после 10 `POST /search` — `GET /metrics` содержит `search_latency_ms_count > 0`, `http_requests_total{handler="/search"}` > 10.
  - Тест: `curl http://localhost:3000/api/dashboards` → 4 дашборда присутствуют.
- **DoD 5**: «`dead`-записи появляются в `search_outbox_dead_digest` (проверка инъекцией заведомо сломанного документа)».
  - Тест: `POST /index` с `content="<invalid>"` и подменить `embedding_model='nonexistent'` через прямой SQL → дождаться 20 попыток reconciler'а → запустить digest-воркер → `SELECT * FROM search_outbox_dead_digest` содержит строку с `error_type='embedding_failed'`.
- **DoD 6**: «Fast-path срабатывает: повторная индексация того же контента не пересчитывает embedding (проверка по логу и `attempts=0` в outbox)».
  - Тест: `POST /index` (doc1, content="hello") → дождаться индексации → `POST /index` (doc1, content="hello") → проверить: `search_outbox` **не содержит** новой записи, `embedding_cache_hits_total` инкрементирован (или `embedding_service.embed_texts` не вызывался — через spy).
- **NFR-замеры** (через `scripts/run_dod_check.sh`):
  1. Latency p99 `/search` ≤ 200 мс — скрипт делает 1000 `POST /search` с разными queries, считает p99 через `numpy.percentile`.
  2. Throughput индексации ≥ 500 doc/min — скрипт делает 100 `POST /index` параллельно (10 одновременных), замеряет время.
  3. Throughput поиска ≥ 100 RPS — скрипт делает 1000 `POST /search` параллельно (50 одновременных), замеряет RPS.
  4. Lag ≤ 30 с — скрипт делает `POST /index`, замеряет время до появления в `/search`.
  5. SLA 99% — скрипт считает `successful_requests / total_requests` за 10 минут.
- Скрипт выводит таблицу: `DoD # | Описание | Статус | Значение | Целевое | Pass/Fail`.
- Exit code 0 — все pass, 1 — хотя бы один fail.

### References

- **ROADMAP §3.4** — Definition of Done (6 пунктов).
- **ROADMAP §3.5** — NFR-таргеты (5 параметров).
- **ROADMAP §9.1** — exit criteria MVP → Critical: DoD + NFR должны быть достигнуты.
- **ARCHITECT §3.4** — implicit reference (DoD синхронизирован с ARCHITECT).

### Acceptance Criteria

- `pytest tests/e2e/test_mvp_dod.py -v` — все 6 DoD-тестов зелёные.
- `bash scripts/run_dod_check.sh` — exit code 0, в выводе все 5 NFR-замеров `Pass`.
- Тесты запускаются против `docker-compose up` (полный стек: postgres, qdrant, redis, api, reconciler, digest, grafana).
- Время выполнения всего suite ≤ 10 минут (включая 60с-ожидания для DoD 3).
- Тесты идемпотентны: повторный запуск на той же БД не падает (чистят за собой через `TRUNCATE documents, search_outbox, search_outbox_dead_digest` в `autouse` fixture).

### Forbidden patterns

- Не использовать `time.sleep(30)` без объяснения — только `poll_until(predicate, timeout=30, interval=2)`.
- Не запускать reconciler/digest вручную в тестах — они должны быть запущены через `docker-compose up` (или как отдельные процессы в `conftest.py`).
- Не оставлять мусор в БД после тестов — `autouse` fixture с `TRUNCATE`.
- Не использовать production-данные — только синтетические seed'ы в тестах.
- Не падать при flaky-таймаутах — использовать `pytest-rerunfailures` для DoD 3 (1 rerun допустим).

### Suggested files

- `tests/e2e/__init__.py`
- `tests/e2e/conftest.py` (фикстуры: `api_client`, `postgres_db`, `qdrant_client`, `redis_client`, `seed_documents`)
- `tests/e2e/test_mvp_dod.py` (6 тестов, по одному на DoD-пункт)
- `scripts/run_dod_check.sh`
- `scripts/run_nfr_load_test.py` (вспомогательный скрипт для NFR-замеров, использует `locust` или `httpx` + `asyncio`)
- `Makefile` (или `tasks.py` для `taskipy`) — `make e2e`, `make nfr`, `make dod`

---

## Приложение A. Чек-лист готовности MVP к сдаче

Перед запуском `P-17` убедиться, что выполнены:

- [ ] **P-00** — `docker-compose up` поднимает стек без ошибок.
- [ ] **P-01..P-03** — `alembic upgrade head` создаёт все таблицы и индексы.
- [ ] **P-04** — Qdrant-коллекция `documents` существует, payload-индексы созданы.
- [ ] **P-05..P-06** — `EmbeddingService.embed_texts(["test"])` возвращает вектор 1024-размера, кэш работает.
- [ ] **P-07..P-08** — `POST /index` и `DELETE /index/{doc_id}` работают, fast-path срабатывает.
- [ ] **P-09..P-11** — `POST /search` возвращает RRF-fused результаты, дедупликация работает, `explain=true` отдаёт debug.
- [ ] **P-12** — `/health/ready` и `/metrics` отвечают.
- [ ] **P-13..P-14** — Reconciler и digest-воркеры запущены, dead-записи агрегируются.
- [ ] **P-15..P-16** — Метрики публикуются, Grafana-дашборды видны.
- [ ] **TRIZ-gates** (ROADMAP §3.3) — все 5 встроены:
  - [ ] `dead`-состояние в outbox (P-02)
  - [ ] Digest-агрегатор dead-записей (P-14)
  - [ ] Fast-path по `content_hash` (P-06, P-07)
  - [ ] `is_active` в `embedding_models` (P-03)
  - [ ] Партиционирование `search_events` (структура заложена в P-01)

## Приложение B. Что НЕ делать в MVP (scope guard)

Список фич, которые **запрещено** реализовывать в этой фазе (они появятся в Critical/Production-Ready/Improvements):

- ❌ Cross-encoder reranker (Critical, ARCHITECT §7.3, ROADMAP §4.2.1)
- ❌ Weighted fusion (Critical, ARCHITECT §7.2, ROADMAP §4.2.2)
- ❌ Circuit breaker для reranker (Critical, ARCHITECT §6.6, ROADMAP §4.2.3)
- ❌ Speculative rerank (Critical, ARCHITECT §6.5, ROADMAP §4.2.4)
- ❌ Фасеты в PostgreSQL (Critical, ARCHITECT §8.4, ROADMAP §4.2.5)
- ❌ Оффлайн-eval / nightly job (Critical, ARCHITECT §11.5, ROADMAP §4.2.6)
- ❌ `wait_for_index` endpoint (Critical, ARCHITECT §14.3, ROADMAP §4.2.7)
- ❌ Degraded mode / lex-only fallback (Critical, ARCHITECT §14.1, ROADMAP §4.2.8)
- ❌ Push-down фильтры (Critical, ARCHITECT §8.2, ROADMAP §4.2.9)
- ❌ Адаптивный throttle при переполнении outbox (Critical, ROADMAP §4.2.10)
- ❌ A/B-тестирование (Production-Ready, ARCHITECT §11.3)
- ❌ Персонализация (Production-Ready, ARCHITECT §10)
- ❌ OTel distributed tracing (Production-Ready, ARCHITECT §11.2)
- ❌ Shadow traffic (Production-Ready, ARCHITECT §11.6)
- ❌ Chaos engineering (Production-Ready, ARCHITECT §11.7)
- ❌ Тенантное tiering (Production-Ready, ARCHITECT §12.6)
- ❌ Канареечный reindex (Production-Ready, ARCHITECT §13.5)
- ❌ Multi-armed bandit для weighted fusion (Improvements)
- ❌ Каскадный выбор стратегии (Improvements, ARCHITECT §7.5)
- ❌ Product quantization (Improvements, ARCHITECT §12.2)
- ❌ CDC-миграция (Improvements)
- ❌ LambdaMART learned ranking (Improvements, ARCHITECT §7.4)

Если в процессе работы над MVP-промптом агент считает, что какая-то из этих фич «нужна прямо сейчас» — это красный флаг. Зафиксировать в `worklog.md` как «отложено до фазы X» и продолжить MVP.
