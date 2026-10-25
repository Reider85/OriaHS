# PROD-PROMPTS — Промпты для ИИ-агента (Claude Code) по реализации Фазы Production-Ready гибридного поиска

> **Источник**: `ARCHITECT.md` v2.0 (TRIZ-applied) + `ROADMAP.md` v1.0 + `MVP-PROMPTS.md` v1.0 + `CRITICAL-PROMPTS.md` v1.0 + `BACKLOG.md` v1.0 + `BUGFIXES-PROMPTS.md` v1.0
> **Целевая фаза**: Production-Ready (ROADMAP §5) — полноценная эксплуатация: персонализация, A/B-фреймворк, distributed tracing, миграции embedding-моделей, shadow traffic, chaos engineering
> **Стек**: Python 3.11+ (FastAPI, SQLAlchemy 2.x async), PostgreSQL 16, Qdrant 1.x, Redis 7, OpenTelemetry SDK, GPU-нода (для cross-encoder + backfill)
> **Целевой агент**: Claude Code
> **Стиль промптов**: spec-driven (Context / Goal / Constraints / References / Acceptance Criteria / Forbidden patterns / Suggested files)
> **Гранулярность**: 1 промпт = 1 задача (~30–120 минут работы агента)
> **Предусловие**: Фазы MVP и Critical завершены (см. `MVP-PROMPTS.md` P-00..P-17, `CRITICAL-PROMPTS.md` C-00..C-14, все DoD §3.4 / §4.4 зелёные, exit criteria §9.1 / §9.2 выполнены, `BUGFIXES-PROMPTS.md` B-00..B-12 применены)

---

## 0. Как пользоваться этим документом

### 0.1. Порядок выполнения

Промпты пронумерованы `PR-00` … `PR-15`. Большинство имеет **upstream-зависимости** от Critical-промптов (C-01..C-14) и от других Production-Ready-промптов. Параллельно можно запускать только промпты из независимых подсистем.

Рекомендуемый порядок:

1. **PR-00** (Phase bootstrap) — обязательно первым: добавляет env-переменные, зависимости в `pyproject.toml`, расширяет `FeatureFlags`, готовит структуру каталогов для персонализации / A/B / tracing / миграций.
2. Параллельно: **PR-01** (Персонализация — `users` + `profile_vector`) и **PR-15** (Negative cache) и **PR-14** (Schema drift контракт-тесты) — три изолированных компонента (ROADMAP §8.3), не блокируют друг друга.
3. Последовательно: **PR-02** (`search_events` партиционирование + архив) — после PR-01, т.к. `search_events` ссылается на `users`.
4. Параллельно: **PR-03** (A/B-фреймворк) и **PR-04** (MMR diversification) — после PR-02; A/B нужен `search_events`, MMR изолирован.
5. **PR-05** (Контекстная персонализация) — после PR-01, расширяет профиль device/time/session.
6. Параллельно: **PR-06** (Distributed tracing OTel) и **PR-12** (Многоязычность enhanced) — изолированы; OTel требует C-12 (метрики), многоязычность — только P-09.
7. **PR-07** (Миграционный фреймворк) — после PR-02 + C-07; самый сложный промпт фазы (blue-green, backfill, канарейка, snapshot, rollback).
8. **PR-08** (Shadow traffic) — после PR-03 (нужен `experiment_id` и A/B-инфраструктура).
9. **PR-09** (Chaos engineering weekly) — после PR-06 + C-09 (нужен OTel для postcondition-чеков и degraded mode для проверки lex-only fallback).
10. Параллельно: **PR-10** (Adaptive K по `tenant_search_stats`) — после PR-02; **PR-11** (Инкрементальные фасеты) — после C-06; **PR-13** (Асимметричное индексирование) — после P-05.
11. **PR-14** (Schema drift контракт-тесты) — изолирован, может быть выполнен в любой момент после P-04.
12. **PR-15** (Negative cache) — изолирован, может быть выполнен в любой момент после P-06.

> **Замечание об изолированных компонентах (ROADMAP §8.3):** PR-04, PR-12, PR-14, PR-15 можно внедрять в любой момент — они не блокируются другими PR-задачами. Это позволяет начать работу над ними параллельно с PR-01/PR-02, если есть свободные ресурсы агента.

### 0.2. Карта промптов

| ID | Компонент ROADMAP §5.2 | Зависимости | ARCHITECT §ref | ROADMAP §ref | Ожидаемый артефакт |
|----|---|---|---|---|---|
| **PR-00** | Phase bootstrap | все C-*, B-* | §17.2, §10, §11, §13 | §5 (вводная) | env-расширение, `pyproject.toml` updates, новые config-секции, структура каталогов `app/services/personalization.py`, `app/services/ab_testing.py`, `app/services/shadow_traffic.py`, `app/observability/tracing.py` |
| **PR-01** | Персонализация — `users` + `profile_vector` (§5.2.1) | C-14 (exit criteria) | §10.1, §10.2, §10.4 | §5.2.1 | миграция `006_users`, `app/db/models/user.py`, `app/services/personalization.py` (EMA worker при клике), `POST /search?personalize=true` |
| **PR-02** | `search_events` — партиционирование + архив (§5.2.2) | PR-01 | §3.1, §11.4 | §5.2.2 | миграция `007_search_events_partitions`, daily partition cron, S3 Parquet архивация 90 дней, `DROP PARTITION` |
| **PR-03** | A/B-фреймворк (§5.2.3) | PR-02 | §11.3 | §5.2.3 | `app/services/ab_testing.py` (sticky distribution по `user_id`, sequential testing), `experiment_id` в `SearchRequest` |
| **PR-04** | MMR anti-bubble diversification (§5.2.4) | PR-01 (мягкая) | §10.5 | §5.2.4 | `app/search/diversify.py` (MMR, λ=0.7), `diversify=true` в API, adaptive `λ` |
| **PR-05** | Контекстная персонализация (§5.2.5) | PR-01 | §10.6 | §5.2.5 | `SearchContext {device, session_id, time_of_day}`, multiple profile vectors, выбор профиля по контексту |
| **PR-06** | Distributed tracing OpenTelemetry (§5.2.6) | C-12 | §11.2 | §5.2.6 | `app/observability/tracing.py` (OTel SDK, spans), export в Tempo, trace↔log correlation через `trace_id` |
| **PR-07** | Миграционный фреймворк embedding-моделей (§5.2.7) | PR-02, C-07 | §13.1–13.6 | §5.2.7 | `app/services/migration.py` (blue-green, backfill-воркер, канареечный reindex 1%, snapshot-on-reindex, rollback), CLI `python -m app.migration` |
| **PR-08** | Shadow traffic (§5.2.8) | PR-03 | §11.6 | §5.2.8 | `app/services/shadow_traffic.py` (параллельный расчёт новой стратегии, лог в `search_events.variant`) |
| **PR-09** | Chaos engineering weekly (§5.2.9) | PR-06, C-09 | §11.7 | §5.2.9 | `tests/chaos/*.py` (kill Qdrant, delay reranker, fill Redis cache), weekly cron, postcondition-чеки в CI |
| **PR-10** | Adaptive K по `tenant_search_stats` (§5.2.10) | PR-02 | §6.4 | §5.2.10 | миграция `008_tenant_search_stats`, `app/search/adaptive_k.py` (adaptive K_lexical/K_vector по doc_count и avg_query_len) |
| **PR-11** | Инкрементальные фасеты (§5.2.11) | C-06 | §8.5 | §5.2.11 | `app/search/facets_incremental.py` (Redis-кэш `facets:{query_hash}:{page}`, TTL 10 мин, incremental merge) |
| **PR-12** | Многоязычность enhanced (§5.2.12) | P-09 | §9.3, §9.4 | §5.2.12 | `app/search/langid_fasttext.py`, смешанные запросы token-level, `cyrtranslit`, Unicode NFC-нормализация |
| **PR-13** | Асимметричное индексирование (§5.2.13) | P-05 | §5.6 | §5.2.13 | `app/embedding/chunking.py` (chunking 512 overlap 50, взвешенное среднее секций title × 3, abstract × 2, body × 1), агрегация `max(score)` по `doc_id` |
| **PR-14** | Schema drift контракт-тесты (§5.2.14) | P-04 | §15.4 | §5.2.14 | `tests/contract/test_schema_drift.py` (payload Qdrant ↔ documents Postgres, единый `QdrantPayload`), CI gate |
| **PR-15** | Negative cache для embeddings (§5.2.15) | P-06 | §5.3 | §5.2.15 | `app/embedding/cache.py` extension (Redis `null` cache TTL 60 сек для сломанных входов) |

### 0.3. Глобальные правила для агента

1. **Перед стартом каждого промпта** агент обязан прочитать соответствующие §ref из `ARCHITECT.md` и `ROADMAP.md` (файлы лежат в `analitics/`). Если в промпте указано `ARCHITECT §10.5` — открыть `analitics/ARCHITECT.md`, найти раздел `### 10.5`, прочитать целиком. Также свериться с `BACKLOG.md` §3 (задачи Production-Ready) и `BUGFIXES-PROMPTS.md` (чтобы не reintroduce исправленные баги).
2. **Не выходить за рамки Production-Ready**. Любые фичи из §6 ROADMAP (Learned ranking LambdaMART, каскадный fusion, multi-armed bandit, tenant tiering, PostgreSQL read replicas, Qdrant sharding, Redis Cluster, CDC Debezium, connection-aware routing) — **запрещены** в этой фазе. Если промпт молчит про фичу — её не реализовывать.
3. **TRIZ-gates Production-Ready встраиваются вместе с фичей** (ROADMAP §5.3 / §10.3), не откладываются:
   - Adaptive `α` в EMA (0.5 для новых, 0.05 для established) — обязательно в PR-01.
   - MMR anti-bubble — обязательно в PR-04.
   - Контекстная персонализация (device/time/session) — обязательно в PR-05.
   - Shadow traffic — обязательно в PR-08.
   - Chaos engineering weekly — обязательно в PR-09.
   - Канареечный reindex (1% перед full) — обязательно в PR-07.
   - Snapshot-on-reindex — обязательно в PR-07.
   - Blue-green коллекции — обязательно в PR-07.
   - Distributed tracing (metrics ↔ logs ↔ traces) — обязательно в PR-06.
   - Adaptive K по `tenant_search_stats` — обязательно в PR-10.
   - Инкрементальные фасеты — обязательно в PR-11.
   - Смешанные запросы (token-level) — обязательно в PR-12.
   - Асимметричное индексирование — обязательно в PR-13.
   - Schema drift контракт-тесты — обязательно в PR-14.
   - Negative cache — обязательно в PR-15.
   - Партиционирование `search_events` по дням — обязательно в PR-02.
4. **Сохранять единый `QdrantPayload`** — pydantic-модель из MVP (P-04). Если для асимметричного индексирования (PR-13) нужны дополнительные поля payload (`chunk_idx`, `section_type`) — расширять модель, не плодить дубликаты. Schema drift контракт-тесты (PR-14) валидируют консистентность.
5. **Все SQL-миграции** — через Alembic, идемпотентные, с `downgrade()`. Нумерация продолжается: `006_users`, `007_search_events_partitions`, `008_tenant_search_stats`, …
6. **Все env-dependent параметры** (personalization α, MMR λ, OTel endpoint, migration batch size, canary threshold, snapshot retention, chaos schedule, adaptive K thresholds) — через `pydantic-settings` (`app/config.py`), никаких хардкодов.
7. **GDPR / privacy** — `profile_vector` — агрегат, не позволяет восстановить конкретные клики. `search_events` хранятся 90 дней, затем архивируются и удаляются. Пользователь может запросить экспорт и удаление данных — `profile_vector` обнуляется. Реализовать endpoint `DELETE /users/{user_id}/gdpr` в PR-01.
8. **Не ломать Critical-контракты**: `POST /index`, `DELETE /index/{doc_id}`, `POST /search` с `fusion="rrf"`, `rerank=false`, `facets=None` должны работать идентично Critical. Новые поля (`personalize`, `diversify`, `experiment_id`, `context`) добавляются как опциональные.
9. **Self-check перед завершением промпта**:
   - `ruff check app/ tests/` — без ошибок.
   - `mypy app/` — без ошибок (strict-режим для нового кода).
   - `pytest tests/unit/<module>_test.py` — зелёный.
   - Если промпт добавляет endpoint: `curl`-проверка против локально поднятого `docker-compose up`.
   - Если промпт добавляет миграцию: `alembic upgrade head` + `alembic downgrade -1` + `alembic upgrade head` — идемпотентность.
10. **После завершения промпта** — краткий отчёт: что создано, какие §ref закрыты, какие TRIZ-gates встроены, какие отклонения от спеки (если есть) и почему. Обновить `BACKLOG.md` — сменить статус задачи на `done` (или `in_progress`).
11. **Защита от регрессий**: после каждого промпта запускать `scripts/run_dod_check_critical.sh` — Critical DoD не должен сломаться. Production-Ready расширяет, но не регрессирует.

### 0.4. Расширение структуры репозитория

Production-Ready-фаза расширяет Critical-структуру (см. `CRITICAL-PROMPTS.md` §0.4). Новые каталоги/файлы отмечены `★`.

```
.
├── ARCHITECT.md
├── ROADMAP.md
├── BACKLOG.md
├── MVP-PROMPTS.md
├── CRITICAL-PROMPTS.md
├── BUGFIXES-PROMPTS.md
├── PROD-PROMPTS.md                   # ★ этот документ
├── docker-compose.yml                # + otel-collector, tempo, loki (PR-06); minio/S3 mock (PR-02, PR-07)
├── pyproject.toml                    # + opentelemetry-sdk, opentelemetry-exporter-otlp, fasttext-langid, cyrtranslit, boto3, pyarrow
├── .env.example                      # + PERSONALIZATION_*, AB_TESTING_*, TRACING_*, MIGRATION_*, SHADOW_*, CHAOS_* переменные
├── alembic/
│   └── versions/
│       ├── 001_documents.py
│       ├── 002_outbox.py
│       ├── 003_embedding_models.py
│       ├── 004_dead_digest.py
│       ├── 005_eval_datasets.py
│       ├── 006_users.py              # ★ PR-01 (users + profile_vector)
│       ├── 007_search_events_partitions.py  # ★ PR-02 (daily partitions + archival job)
│       ├── 008_tenant_search_stats.py # ★ PR-10 (tenant stats table)
│       └── 009_search_events_variant.py  # ★ PR-08 (variant column для shadow traffic)
├── app/
│   ├── api/
│   │   ├── routes/
│   │   │   ├── index.py
│   │   │   ├── search.py             # обновлён в PR-01, PR-03, PR-04, PR-05
│   │   │   ├── health.py
│   │   │   └── users.py              # ★ PR-01 (GDPR delete, profile export)
│   │   ├── schemas.py                # + personalize, diversify, experiment_id, context, variant (PR-01..PR-05, PR-08)
│   │   └── deps.py                   # + PersonalizationService, ABTestingService, ShadowTrafficService deps
│   ├── config.py                     # + PersonalizationConfig, ABTestingConfig, TracingConfig, MigrationConfig, ShadowTrafficConfig, ChaosConfig, AdaptiveKConfig, IncrementalFacetsConfig, NegativeCacheConfig
│   ├── search/
│   │   ├── fusion.py
│   │   ├── orchestrator.py           # обновлён в PR-01, PR-04, PR-05, PR-08, PR-10, PR-13
│   │   ├── facets.py
│   │   ├── facets_incremental.py     # ★ PR-11
│   │   ├── pushdown.py
│   │   ├── speculative.py
│   │   ├── diversify.py              # ★ PR-04 (MMR)
│   │   ├── adaptive_k.py             # ★ PR-10
│   │   └── langid_fasttext.py        # ★ PR-12
│   ├── embedding/
│   │   ├── service.py                # обновлён в PR-13 (chunking)
│   │   ├── cache.py                  # обновлён в PR-15 (negative cache)
│   │   └── chunking.py               # ★ PR-13 (asymmetric indexing)
│   ├── services/
│   │   ├── indexing.py               # обновлён в PR-13 (chunking hook)
│   │   ├── qdrant.py
│   │   ├── throttle.py
│   │   ├── personalization.py        # ★ PR-01 (EMA profile vector worker)
│   │   ├── ab_testing.py             # ★ PR-03 (sticky distribution, sequential testing)
│   │   ├── shadow_traffic.py        # ★ PR-08 (parallel strategy eval)
│   │   └── migration.py              # ★ PR-07 (blue-green, backfill, canary, snapshot, rollback)
│   ├── db/
│   │   ├── queries/
│   │   │   ├── outbox.py
│   │   │   ├── eval.py
│   │   │   ├── tenants.py
│   │   │   ├── documents.py
│   │   │   ├── embedding_models.py
│   │   │   ├── users.py              # ★ PR-01 (CRUD users, profile_vector)
│   │   │   ├── search_events.py      # ★ PR-02 (insert, partition management, archive)
│   │   │   └── tenant_stats.py       # ★ PR-10 (upsert doc_count, avg_query_len)
│   │   └── models/
│   │       ├── document.py
│   │       ├── eval_dataset.py
│   │       ├── eval_result.py
│   │       ├── user.py               # ★ PR-01
│   │       ├── search_event.py       # ★ PR-02
│   │       └── tenant_search_stat.py # ★ PR-10
│   ├── observability/
│   │   ├── metrics.py                # расширяем в PR-01, PR-03, PR-06, PR-08
│   │   ├── logging.py                # обновлён в PR-06 (trace_id из OTel)
│   │   └── tracing.py                # ★ PR-06 (OTel SDK, spans, export в Tempo)
│   └── migration/                    # ★ PR-07
│       ├── __init__.py
│       ├── __main__.py               # CLI: init, backfill, canary, promote, rollback
│       ├── backfill_worker.py
│       ├── canary_check.py
│       └── snapshot.py
├── eval/
│   ├── datasets/
│   │   └── baseline_v1.jsonl
│   └── baselines/
│       └── baseline_v1.json
├── grafana/provisioning/dashboards/
│   ├── mvp-*.json
│   ├── critical-*.json
│   ├── prod-personalization.json     # ★ PR-01
│   ├── prod-ab-testing.json          # ★ PR-03
│   ├── prod-tracing.json             # ★ PR-06
│   ├── prod-migration.json           # ★ PR-07
│   └── prod-search-events.json       # ★ PR-02
├── monitoring/
│   └── otel-collector-config.yaml    # ★ PR-06
├── scripts/
│   ├── init_qdrant.py
│   ├── run_dod_check.sh
│   ├── run_dod_check_critical.sh
│   ├── run_dod_check_prod.sh         # ★ PR-15 (DoD Production-Ready)
│   ├── run_nightly_eval.sh
│   ├── run_partition_management.py   # ★ PR-02 (daily partition + archival cron)
│   └── run_chaos_weekly.py           # ★ PR-09 (weekly chaos cron)
├── tests/
│   ├── unit/
│   │   ├── test_personalization_test.py          # ★ PR-01
│   │   ├── test_search_events_test.py            # ★ PR-02
│   │   ├── test_ab_testing_test.py               # ★ PR-03
│   │   ├── test_mmr_diversify_test.py            # ★ PR-04
│   │   ├── test_context_personalization_test.py  # ★ PR-05
│   │   ├── test_tracing_test.py                  # ★ PR-06
│   │   ├── test_migration_framework_test.py      # ★ PR-07
│   │   ├── test_shadow_traffic_test.py           # ★ PR-08
│   │   ├── test_adaptive_k_test.py               # ★ PR-10
│   │   ├── test_incremental_facets_test.py       # ★ PR-11
│   │   ├── test_fasttext_langid_test.py          # ★ PR-12
│   │   ├── test_chunking_test.py                 # ★ PR-13
│   │   └── test_negative_cache_test.py           # ★ PR-15
│   ├── integration/
│   │   ├── test_personalized_search_test.py      # ★ PR-01
│   │   ├── test_search_events_archive_test.py    # ★ PR-02
│   │   ├── test_ab_experiment_test.py            # ★ PR-03
│   │   ├── test_mmr_e2e_test.py                  # ★ PR-04
│   │   ├── test_migration_canary_test.py        # ★ PR-07
│   │   └── test_shadow_traffic_e2e_test.py       # ★ PR-08
│   ├── contract/
│   │   └── test_schema_drift.py                  # ★ PR-14
│   ├── chaos/                                    # ★ PR-09
│   │   ├── test_kill_qdrant.py
│   │   ├── test_delay_reranker.py
│   │   └── test_fill_redis_cache.py
│   └── e2e/
│       ├── test_critical_dod.py
│       └── test_prod_dod.py                       # ★ PR-15
└── s3/
    └── snapshots/                                # ★ PR-07 (Qdrant snapshots, retention 30 дней)
```

---

## PR-00 — Phase bootstrap: конфигурация, зависимости, структура каталогов

> **Компонент ROADMAP §5 (вводная)** | **Артефакт**: расширение `app/config.py` + `pyproject.toml` + `.env.example` + заготовки модулей

### Context

Production-Ready-фаза добавляет персонализацию (EMA profile vectors), A/B-фреймворк (sticky distribution), distributed tracing (OpenTelemetry SDK), миграционный фреймворк (blue-green, backfill, snapshot), shadow traffic, chaos engineering, adaptive K, инкрементальные фасеты, многоязычность enhanced, асимметричное индексирование, schema drift контракт-тесты и negative cache. Все эти параметры должны быть сконфигурированы до того, как начнётся реализация компонентов — иначе каждый последующий промпт будет тащить свой минимальный конфиг и они разойдутся. Также фаза вводит новые внешние зависимости (OTel SDK, fasttext-langid, cyrtranslit, boto3 для S3, pyarrow для Parquet).

### Goal

1. Расширить `app/config.py` новыми секциями: `PersonalizationConfig`, `ABTestingConfig`, `TracingConfig`, `MigrationConfig`, `ShadowTrafficConfig`, `ChaosConfig`, `AdaptiveKConfig`, `IncrementalFacetsConfig`, `NegativeCacheConfig`. Расширить `FeatureFlags` новыми флагами.
2. Добавить зависимости в `pyproject.toml`: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-redis`, `fasttext-langid`, `cyrtranslit`, `boto3`, `pyarrow`.
3. Расширить `app/api/schemas.py::SearchRequest` опциональными полями: `personalize: bool = False`, `diversify: bool = False`, `experiment_id: str | None = None`, `context: SearchContext | None = None`. Создать pydantic-модель `SearchContext` с полями `device: Literal["desktop","mobile","tablet"] | None`, `session_id: str | None`, `time_of_day: Literal["morning","afternoon","evening","night"] | None`.
4. Расширить `SearchResponse` полем `variant: str | None = None` (для shadow traffic — PR-08).
5. Создать пустые заготовки модулей (с docstring + `pass` / `NotImplementedError`): `app/services/personalization.py`, `app/services/ab_testing.py`, `app/services/shadow_traffic.py`, `app/services/migration.py`, `app/observability/tracing.py`, `app/search/diversify.py`, `app/search/adaptive_k.py`, `app/search/facets_incremental.py`, `app/search/langid_fasttext.py`, `app/embedding/chunking.py`.
6. Расширить `docker-compose.yml` сервисами: `otel-collector`, `tempo`, `loki`, `minio` (S3-совместимый mock для snapshots и Parquet-архивов в dev).

### Constraints

- `PersonalizationConfig`:
  - `ema_alpha_new: float = 0.5` (быстрое обучение для новых пользователей, < 10 кликов)
  - `ema_alpha_established: float = 0.05` (медленная адаптация для established, ≥ 10 кликов)
  - `new_user_click_threshold: int = 10` (порог перехода new → established)
  - `profile_weight: float = 0.3` (вес profile_vector в final_score: `0.7 * fused + 0.3 * cosine(profile, doc)`)
  - `fused_weight: float = 0.7`
  - `cold_start_enabled: bool = True` (pure fusion при отсутствии profile_vector)
  - `profile_dimension: int = 1024` (должен совпадать с `EXPECTED_DIMENSION`)
  - `gdpr_delete_endpoint_enabled: bool = True`
- `ABTestingConfig`:
  - `sticky_hash_buckets: int = 1000` (консистентное хеширование по `user_id`)
  - `sequential_testing_enabled: bool = True`
  - `significance_level: float = 0.05`
  - `min_samples_per_variant: int = 100`
  - `max_parallel_experiments: int = 3`
- `TracingConfig`:
  - `otel_endpoint: str = "http://otel-collector:4317"` (gRPC)
  - `service_name: str = "oriahs"`
  - `export_interval_ms: int = 5000`
  - `sample_rate: float = 1.0` (100% в dev; в prod — 0.1 через `OTEL_TRACES_SAMPLER_ARG`)
- `MigrationConfig`:
  - `backfill_batch_size: int = 1000`
  - `backfill_speed_doc_per_hour: int = 50000` (ожидаемая скорость на GPU-ноде)
  - `canary_percentage: float = 0.01` (1% перед full reindex)
  - `canary_min_similarity: float = 0.6` (если cosine sim старой и новой модели < 0.6 — стоп)
  - `canary_max_similarity: float = 0.95` (если > 0.95 — модель слишком похожа, не тратить ресурсы)
  - `snapshot_retention_days: int = 30`
  - `snapshot_s3_bucket: str = "oriahs-qdrant-snapshots"`
  - `rollback_timeout_minutes: int = 30`
- `ShadowTrafficConfig`:
  - `enabled: bool = False` (по умолчанию off — включается per-experiment)
  - `strategies: list[str] = []` (например `["rrf", "weighted+rerank"]`)
  - `log_variant: bool = True` (логировать оба результата в `search_events.variant`)
- `ChaosConfig`:
  - `schedule_cron: str = "0 6 * * 0"` (каждое воскресенье 06:00 UTC)
  - `environment: str = "staging"` (только non-prod!)
  - `qdrant_kill_seconds: int = 60`
  - `reranker_delay_ms: int = 1000`
  - `redis_fill_percentage: float = 0.95`
- `AdaptiveKConfig`:
  - `small_tenant_doc_threshold: int = 10000` (< 10k → K=30)
  - `large_tenant_doc_threshold: int = 1000000` (> 1M → K=100)
  - `short_query_len_threshold: int = 5` (< 5 токенов → повышаем K_lexical)
  - `long_query_len_threshold: int = 20` (> 20 → повышаем K_vector)
  - `k_small: int = 30`
  - `k_large: int = 100`
- `IncrementalFacetsConfig`:
  - `cache_ttl_seconds: int = 600` (10 минут)
  - `cache_key_prefix: str = "facets"`
- `NegativeCacheConfig`:
  - `null_ttl_seconds: int = 60`
  - `enabled: bool = True`
- `FeatureFlags` (расширение):
  - `personalization_enabled: bool = True`
  - `ab_testing_enabled: bool = True`
  - `diversify_enabled: bool = True`
  - `context_personalization_enabled: bool = True`
  - `tracing_enabled: bool = True`
  - `migration_framework_enabled: bool = True`
  - `shadow_traffic_enabled: bool = False`
  - `adaptive_k_enabled: bool = True`
  - `incremental_facets_enabled: bool = True`
  - `negative_cache_enabled: bool = True`
  - `asymmetric_indexing_enabled: bool = True`
- `pyproject.toml` dependencies (добавить):
  - `opentelemetry-sdk>=1.25.0`
  - `opentelemetry-exporter-otlp-proto-grpc>=1.25.0`
  - `opentelemetry-instrumentation-fastapi>=0.46b0`
  - `opentelemetry-instrumentation-sqlalchemy>=0.46b0`
  - `opentelemetry-instrumentation-redis>=0.46b0`
  - `fasttext-langid>=1.0.5`
  - `cyrtranslit>=1.1.0`
  - `boto3>=1.34.0`
  - `pyarrow>=15.0.0`
- `SearchContext` модель:
  ```python
  class SearchContext(BaseModel):
      device: Literal["desktop", "mobile", "tablet"] | None = None
      session_id: str | None = None
      time_of_day: Literal["morning", "afternoon", "evening", "night"] | None = None
  ```

### References

- **ARCHITECT §17.2** — `config.yaml` приложения, расширить секциями для personalization / A/B / tracing / migration.
- **ARCHITECT §10** — Персонализация ( цели, profile_vector, EMA, privacy).
- **ARCHITECT §11.2** — Distributed tracing параметры.
- **ARCHITECT §11.3** — A/B-тестирование параметры.
- **ARCHITECT §13.1–13.6** — Миграционный фреймворк параметры.
- **ROADMAP §5 (вводная)** — цели фазы Production-Ready.
- **ROADMAP §5.2.1–5.2.15** — параметры каждого компонента.
- **BACKLOG.md §3.1** — задачи PR-01..PR-15, зависимости, артефакты.

### Acceptance Criteria

- `app/config.py` содержит все 9 новых секций с указанными дефолтами.
- `AppConfig` включает все новые секции через `Annotated[..., Field(default_factory=...)]`.
- `pyproject.toml` — `uv sync` выполняется без ошибок, новые зависимости установлены.
- `.env.example` обновлён: добавлены все новые env-переменные с примерами значений и комментариями.
- `app/api/schemas.py::SearchRequest` имеет поля `personalize`, `diversify`, `experiment_id`, `context` (все опциональные, дефолты safe).
- `app/api/schemas.py::SearchResponse` имеет поле `variant: str | None = None`.
- `app/api/schemas.py::SearchContext` существует с тремя полями.
- Все 10 заготовок модулей созданы (импортируются без `ImportError`, содержат docstring).
- `docker-compose.yml` содержит сервисы `otel-collector`, `tempo`, `loki`, `minio` с healthchecks.
- `ruff check`, `mypy app/`, `pytest` — зелёные (без new failures; существующие тесты не сломаны).
- Smoke-тест `tests/unit/test_prod_config_test.py` — загружает конфиг из env и проверяет все 9 новых секций и их дефолты.
- `POST /search` без новых полей работает идентично Critical (regression test из `tests/e2e/test_critical_dod.py` — зелёный).

### Forbidden patterns

- Не хардкодить параметры в коде — только через `pydantic-settings`.
- Не добавлять фичи из §6 ROADMAP (Learned ranking, каскадный fusion, multi-armed bandit, tiering, replicas, sharding, CDC).
- Не использовать `Literal["cascade", "learned"]` в `fusion` — это Improvements.
- Не реализовывать логику в заготовках модулей — только интерфейс/документация, реализация в соответствующих промптах PR-01..PR-15.
- Не создавать `users` или `tenant_search_stats` таблицы в этом промпте — миграции создаются в PR-01 и PR-10 соответственно.
- Не включать `shadow_traffic_enabled` по умолчанию — это per-experiment флаг (PR-08).
- Не включать `ChaosConfig.environment = "production"` — chaos только в non-prod.

### Suggested files

- `app/config.py` — расширить 9 новыми секциями + `FeatureFlags`
- `pyproject.toml` — обновить dependencies
- `.env.example` — обновить
- `app/api/schemas.py` — расширить `SearchRequest`, `SearchResponse`, добавить `SearchContext`
- `docker-compose.yml` — добавить otel-collector, tempo, loki, minio
- `monitoring/otel-collector-config.yaml` — новый
- `app/services/personalization.py` — заготовка
- `app/services/ab_testing.py` — заготовка
- `app/services/shadow_traffic.py` — заготовка
- `app/services/migration.py` — заготовка
- `app/observability/tracing.py` — заготовка
- `app/search/diversify.py` — заготовка
- `app/search/adaptive_k.py` — заготовка
- `app/search/facets_incremental.py` — заготовка
- `app/search/langid_fasttext.py` — заготовка
- `app/embedding/chunking.py` — заготовка
- `tests/unit/test_prod_config_test.py`

---

## PR-01 — Персонализация: `users` + `profile_vector` + EMA worker

> **Компонент ROADMAP §5.2.1** | **Артефакт**: миграция `006_users`, `app/db/models/user.py`, `app/services/personalization.py`, `POST /search?personalize=true`, `DELETE /users/{user_id}/gdpr`

### Context

Персонализация — главный quality-буст Production-Ready-фазы: позволяет ранжировать документы по релевантности конкретному пользователю, а не только запросу. `profile_vector` — EMA-агрегат кликнутых документов: `profile = α * embedding(clicked_doc) + (1-α) * profile`, где `α` адаптивен (0.5 для новых пользователей, 0.05 для established). Для cold-start пользователей (нет кликов) персонализация отключается, используется pure fusion. Архитектурно профиль — это агрегат (не позволяет восстановить конкретные клики), что критично для GDPR. Re-ranking: `final_score = 0.7 * fused_score + 0.3 * cosine(user_profile, doc_vector)`. Воркер обновления запускается при каждом клике (через `search_events` с `clicked_doc_id`).

### Goal

1. Создать Alembic-миграцию `006_users`: таблица `users` с `id UUID PK`, `tenant_id UUID`, `profile_vector VECTOR(1024)`, `profile_version INT DEFAULT 0`, `click_count INT DEFAULT 0`, `created_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ`. Установить расширение `pgvector`. Индекс на `(tenant_id, id)`.
2. Создать SQLAlchemy-модель `app/db/models/user.py::User` с `pgvector` ORM-поддержкой.
3. Создать `app/db/queries/users.py` с функциями: `get_by_id`, `get_or_create`, `update_profile_vector`, `reset_profile` (GDPR), `increment_click_count`.
4. Реализовать `app/services/personalization.py::PersonalizationService`:
   - `async def get_profile_vector(self, user_id: UUID, tenant_id: UUID) -> np.ndarray | None` — возвращает `profile_vector` или `None` (cold-start).
   - `async def update_on_click(self, user_id: UUID, tenant_id: UUID, clicked_doc_id: UUID, embedding_service: EmbeddingService) -> None` — EMA-обновление: вычисляет embedding кликнутого документа, обновляет `profile_vector` с адаптивным `α` (0.5 если `click_count < 10`, иначе 0.05), инкрементирует `click_count` и `profile_version`.
   - `async def personalize_scores(self, fused_scores: list[tuple[UUID, float]], user_id: UUID, tenant_id: UUID, doc_vectors: dict[UUID, np.ndarray]) -> list[tuple[UUID, float]]` — применяет формулу `0.7 * fused + 0.3 * cosine(profile, doc)`. Для cold-start (profile is None) — возвращает `fused_scores` без изменений.
   - `async def gdpr_delete(self, user_id: UUID, tenant_id: UUID) -> None` — обнуляет `profile_vector`, сбрасывает `click_count` и `profile_version` до 0.
5. Интегрировать в `SearchOrchestrator`: при `req.personalize=True` и `req.user_id is not None` и `feature_flags.personalization_enabled` — после fusion загрузить `profile_vector`, вычислить cosine similarity с doc_vectors (из Qdrant payload или re-embed), применить `personalize_scores`. В `debug` добавить `personalized=True/False`, `profile_version`, `cold_start=True/False`.
6. Добавить endpoint `DELETE /users/{user_id}` (GDPR delete) в `app/api/routes/users.py` — вызывает `PersonalizationService.gdpr_delete()`.
7. Добавить endpoint `GET /users/{user_id}/profile` — экспорт профиля (для GDPR right to access).

### Constraints

- `profile_vector` хранится как `VECTOR(1024)` в PostgreSQL через `pgvector` расширение. Размерность должна совпадать с `PersonalizationConfig.profile_dimension` и `EXPECTED_DIMENSION` (1024).
- EMA формула: `new_profile = alpha * embedding(clicked_doc) + (1 - alpha) * old_profile`. `alpha` выбирается по `click_count`: `alpha = ema_alpha_new` если `click_count < new_user_click_threshold`, иначе `ema_alpha_established`.
- Векторы должны быть L2-нормализованными (bge-m3 возвращает нормализованные embeddings — `EmbeddingService` уже вызывает `normalize_embeddings=True`). Cosine similarity = dot product для нормализованных векторов.
- `update_on_click` — асинхронный, не блокирует search response. Вызывается из `search_events` ingestion pipeline (PR-02) или отдельным воркером.
- Cold-start: если `profile_vector IS NULL` или `click_count == 0` — персонализация отключается, `personalize_scores` возвращает исходный `fused_scores`, в `debug` ставится `cold_start=True`.
- GDPR: `gdpr_delete` обнуляет `profile_vector` (SET NULL), `click_count = 0`, `profile_version = 0`. Не удаляет строку `users` — сохраняется `tenant_id` для аудит-лога.
- History-aware boost (fallback при отсутствии `profile_vector`, ARCHITECT §10.3): `ORDER BY CASE WHEN category = ANY($recent_categories) THEN 1.0 ELSE 0.0 END DESC, fused_score DESC` — реализовать опционально, если `recent_categories` доступны из `search_events`.
- Для загрузки `doc_vectors` в `personalize_scores`: предпочтительно re-embed топ-K документов через `EmbeddingService.embed_texts` (кэш сработает через content-hash). Альтернатива — хранить вектор в Qdrant payload (но это дублирование данных). Зафиксировать: re-embed через кэш.
- `profile_version` инкрементируется при каждом `update_on_click` — используется для инвалидации кэшей (если профиль кэшируется в Redis).
- Логирование: `logger.info("Profile updated", extra={"user_id": ..., "click_count": ..., "alpha": ..., "profile_version": ...})`.
- Метрики: `profile_vector_updates_total` (Counter), `personalized_searches_total` (Counter, label `cold_start`), `profile_vector_dimension` (Gauge, для мониторинга).

### References

- **ARCHITECT §10.1** — User profile vector (схема, EMA, α).
- **ARCHITECT §10.2** — Re-ranking по user profile (формула `0.7 * fused + 0.3 * cosine`).
- **ARCHITECT §10.3** — History-aware boost (fallback).
- **ARCHITECT §10.4** — Privacy (агрегат, GDPR).
- **ROADMAP §5.2.1** — параметры: `α=0.1` default (но в PR-01 — adaptive: 0.5 new, 0.05 established), `0.7/0.3` weights, cold-start, history-aware boost.
- **ROADMAP §5.6** — риск GDPR / privacy: mitigation — агрегат, retention 90 дней (для `search_events`), GDPR-delete.
- **BACKLOG.md §3.1 PR-01** — зависимости, артефакты.

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу `users` с `pgvector` расширением и колонкой `profile_vector VECTOR(1024)`.
- `alembic downgrade -1` корректно откатывает миграцию (DROP TABLE users, DROP EXTENSION pgvector если больше не используется).
- `PersonalizationService.update_on_click()` — после 3 кликов `profile_vector` не NULL, `click_count=3`, `profile_version=3`.
- `α=0.5` для `click_count < 10`, `α=0.05` для `click_count >= 10` (проверить через метрику/лог).
- `PersonalizationService.personalize_scores()` — для cold-start пользователя (нет кликов) возвращает исходный `fused_scores` без изменений, `debug.cold_start=True`.
- `PersonalizationService.personalize_scores()` — для established пользователя меняет порядок (документы ближе к профилю получают выше score).
- `POST /search` с `personalize=true`, `user_id=<existing>` — response hits отсортированы с учётом профиля, `debug.personalized=True`.
- `POST /search` с `personalize=true`, `user_id=None` — персонализация пропускается, `debug.personalized=False` (no user_id).
- `POST /search` с `personalize=true`, `feature_flags.personalization_enabled=False` — персонализация пропускается.
- `DELETE /users/{user_id}` — обнуляет `profile_vector`, `click_count=0`, `profile_version=0`. Возвращает `200`.
- `GET /users/{user_id}/profile` — возвращает `user_id`, `click_count`, `profile_version`, `profile_vector` (как list[float] или None).
- `POST /search` без `personalize` — работает идентично Critical (regression test зелёный).
- Метрики `profile_vector_updates_total`, `personalized_searches_total` — экспонируются в `/metrics`.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_personalization_test.py`, `pytest tests/integration/test_personalized_search_test.py` — зелёные.

### Forbidden patterns

- Не хранить `profile_vector` в Redis как источник истины — PostgreSQL `users` таблица единственный source of truth; Redis — только опциональный read-through кэш (с инвалидацией по `profile_version`).
- Не восстанавливать конкретные клики из `profile_vector` — это агрегат, обратное вычисление невозможно по design.
- Не блокировать search response на `update_on_click` — EMA-обновление асинхронное (через background task или отдельный воркер).
- Не применять персонализацию для анонимных запросов (`user_id is None`) — privacy-first.
- Не использовать `profile_vector` других пользователей — строго изолировано по `tenant_id` + `user_id`.
- Не забывать GDPR-delete endpoint — это обязательное требование (ARCHITECT §10.4).
- Не дублировать `EmbeddingService` — использовать существующий инстанс через `Depends`.

### Suggested files

- `alembic/versions/006_users.py` — миграция
- `app/db/models/user.py` — SQLAlchemy модель `User`
- `app/db/queries/users.py` — CRUD queries
- `app/services/personalization.py` — `PersonalizationService` (заменить заготовку из PR-00)
- `app/api/routes/users.py` — `DELETE /users/{user_id}`, `GET /users/{user_id}/profile`
- `app/api/deps.py` — `get_personalization_service()` factory
- `app/search/orchestrator.py` — интеграция `personalize_scores` после fusion
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_personalization_test.py`
- `tests/integration/test_personalized_search_test.py`
- `tests/conftest.py` — фикстуры `user_with_profile`, `cold_start_user`

---

## PR-02 — `search_events`: партиционирование по дням + S3 Parquet архивация

> **Компонент ROADMAP §5.2.2** | **Артефакт**: миграция `007_search_events_partitions`, `app/db/queries/search_events.py`, `scripts/run_partition_management.py`, S3 Parquet архивация

### Context

Таблица `search_events` была создана в MVP (P-01) как пустая партиционированная структура с `DEFAULT` партицией. На фазе Production-Ready она наполняется реальными данными: каждый `POST /search` логирует запрос, а каждый клик по результату логирует `clicked_doc_id` + `position`. При росте трафика (500+ RPS) таблица быстро разбухает — без daily-партиционирования и архивации `VACUUM` и `DELETE` станут узким местом. Решение: RANGE-партиционирование по `created_at` (daily), партиции старше 90 дней архивируются в S3 (Parquet) и удаляются через `DROP PARTITION` (O(1) операция, не требует VACUUM). `search_events` используется и для A/B-анализа (PR-03), и для обучения LambdaMART (Improvements IM-01).

### Goal

1. Создать Alembic-миграцию `007_search_events_partitions`: заполнить структуру из P-01 — создать daily-партиции на ближайшие 30 дней (через `CREATE TABLE search_events_YYYYMMDD PARTITION OF search_events FOR VALUES FROM ... TO ...`), настроить партиционирование по умолчанию.
2. Реализовать `app/db/queries/search_events.py` с функциями:
   - `async def insert_search_event(session, user_id, tenant_id, query_text, query_lang, top_doc_ids, fusion_strategy, experiment_id, latency_ms) -> int` — вставляет строку в `search_events` (попадает в текущую daily-партицию автоматически).
   - `async def insert_click_event(session, user_id, tenant_id, query_text, clicked_doc_id, position, fusion_strategy, experiment_id) -> None` — вставляет click-event.
   - `async def create_daily_partition(session, date: date) -> None` — создаёт партицию на указанную дату (для cron-воркера).
   - `async def drop_partition_older_than(session, days: int) -> list[str]` — возвращает список партиций старше `days`, готовых к архивации.
   - `async def archive_partition_to_s3(session, partition_name: str, s3_bucket: str) -> str` — выгружает партицию в Parquet, загружает в S3, возвращает S3 key.
   - `async def drop_partition(session, partition_name: str) -> None` — `DROP TABLE search_events_YYYYMMDD` (O(1)).
3. Реализовать `scripts/run_partition_management.py` — cron-воркер (запуск daily 01:00 UTC):
   - Создаёт партиции на следующие 7 дней (proactive).
   - Архивирует партиции старше 90 дней в S3 Parquet.
   - Удаляет архивированные партиции через `DROP PARTITION`.
   - Логирует результат, обновляет метрики.
4. Интегрировать `insert_search_event` в `SearchOrchestrator` — после завершения поиска (успешного или degraded) логирует запрос. `insert_click_event` вызывается из отдельного endpoint `POST /search/click` (клиент сообщает о клике).
5. Добавить endpoint `POST /search/click` — принимает `{user_id, query, clicked_doc_id, position, experiment_id}`, вызывает `insert_click_event` + `PersonalizationService.update_on_click` (из PR-01, если персонализация включена).

### Constraints

- Партиционирование: `RANGE (created_at)`, daily granularity. Каждая партиция: `search_events_YYYYMMDD`.
- Партиция `search_events_default` (создана в P-01) остаётся как safety net — туда попадают строки с `created_at` вне диапазона любой daily-партиции (не должно происходить при корректной работе cron, но защищает от ошибок).
- Архивация: партиция старше 90 дней → `SELECT * FROM search_events_YYYYMMDD` → конвертация в Parquet через `pyarrow` → загрузка в S3 (`s3://oriahs-search-events-archive/YYYYMMDD.parquet`) → `DROP TABLE search_events_YYYYMMDD`.
- Parquet-схема: колонки `id`, `user_id`, `tenant_id`, `query_text`, `query_lang`, `top_doc_ids` (как string array), `clicked_doc_id`, `position`, `fusion_strategy`, `experiment_id`, `latency_ms`, `created_at`.
- S3-клиент: `boto3` async wrapper (через `aioboto3` или `asyncio.to_thread`). Bucket: `oriahs-search-events-archive`. Retention: 1 год (после — S3 lifecycle rule удаляет).
- `insert_search_event` — fire-and-forget (не блокирует search response). Реализовать через `asyncio.create_task` с error handling (ошибка логирования запроса не должна ломать search).
- `insert_click_event` — синхронная (клиент ждёт подтверждения клика), но с `statement_timeout = 100ms`.
- Индексы (уже созданы в P-01): `search_events_user_time_idx (user_id, created_at DESC)`, `search_events_exp_idx (experiment_id, created_at DESC)`. Проверить, что они создаются на каждой партиции.
- `top_doc_ids` — `UUID[]`, хранится как PostgreSQL array.
- Метрики: `search_events_inserted_total` (Counter, label `fusion_strategy`), `search_events_clicks_total` (Counter), `search_events_partitions_archived_total` (Counter), `search_events_partitions_dropped_total` (Counter), `search_events_rows_total` (Gauge — текущее количество строк во всех партициях, обновляется cron'ом).
- Для dev-окружения без S3: `minio` (docker-compose сервис из PR-00) как S3-совместимый mock. `boto3` endpoint_url = `http://minio:9000`.

### References

- **ARCHITECT §3.1** — схема `search_events` (поля, индексы, партиционирование).
- **ARCHITECT §11.4** — логирование запросов (поля, retention).
- **ROADMAP §5.2.2** — параметры: daily partitions, 90 дней retention, S3 Parquet, `DROP PARTITION`.
- **ROADMAP §5.6** — риск «партиционирование требует операций DBA»: mitigation — автоматизация через cron, chaos-сценарий «удалить партицию в пиковый час» (PR-09).
- **BACKLOG.md §3.1 PR-02** — зависимости (PR-01), артефакты.
- **MVP-PROMPTS P-01** — структура `search_events` (PARTITION BY RANGE, DEFAULT партиция).

### Acceptance Criteria

- `alembic upgrade head` создаёт daily-партиции на ближайшие 30 дней от текущей даты.
- `alembic downgrade -1` корректно откатывает (DROP партиций, но не саму `search_events` таблицу — она из P-01).
- `insert_search_event()` — вставляет строку, она попадает в партицию текущего дня (`SELECT tablename FROM pg_tables WHERE tablename LIKE 'search_events_%'` — находит партицию).
- `POST /search` — после ответа в `search_events` появляется строка с `query_text`, `fusion_strategy`, `latency_ms`, `top_doc_ids`.
- `POST /search/click` — вставляет click-event с `clicked_doc_id` и `position`, возвращает `200`.
- `create_daily_partition(date)` — создаёт партицию `search_events_YYYYMMDD` с корректным RANGE.
- `drop_partition_older_than(90)` — возвращает список партиций старше 90 дней.
- `archive_partition_to_s3()` — выгружает партицию в Parquet, загружает в S3/MinIO, возвращает S3 key. Проверить: `aws s3 ls s3://oriahs-search-events-archive/` (через minio mc) показывает файл.
- `drop_partition()` — удаляет партицию (O(1)), `DROP TABLE search_events_YYYYMMDD`.
- `scripts/run_partition_management.py` — создаёт партиции на 7 дней вперёд, архивирует старые, удаляет. Запуск: `python scripts/run_partition_management.py` — exit 0.
- Метрики `search_events_inserted_total`, `search_events_clicks_total`, `search_events_partitions_archived_total` — экспонируются.
- `POST /search` latency p99 не деградировала (fire-and-forget логирование не блокирует). Regression test: `tests/e2e/test_critical_dod.py` — p99 ≤ 350ms с rerank.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_search_events_test.py`, `pytest tests/integration/test_search_events_archive_test.py` — зелёные.

### Forbidden patterns

- Не использовать `DELETE FROM search_events WHERE created_at < ...` — это full-scan + WAL bloat. Только `DROP PARTITION`.
- Не блокировать search response на `insert_search_event` — только fire-and-forget через `asyncio.create_task`.
- Не архивировать партиции, в которых есть данные за сегодня (текущий день) — только завершённые дни.
- Не удалять `search_events_default` партицию — это safety net.
- Не хранить `top_doc_ids` как JSON — только `UUID[]` (PostgreSQL native array, эффективнее).
- Не забывать S3 lifecycle rule (retention 1 год) — задокументировать в `docker-compose.yml` minio config.
- Не логировать `query_text` для запросов с PII без хеширования — если tenant помечен как PII-sensitive, хешировать `query_text` (опционально через config flag).

### Suggested files

- `alembic/versions/007_search_events_partitions.py` — миграция
- `app/db/models/search_event.py` — SQLAlchemy модель `SearchEvent` (отражает партиционированную таблицу)
- `app/db/queries/search_events.py` — insert, partition management, archive
- `app/api/routes/search.py` — добавить `POST /search/click` endpoint
- `app/api/schemas.py` — `ClickRequest` модель
- `scripts/run_partition_management.py` — cron-воркер
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_search_events_test.py`
- `tests/integration/test_search_events_archive_test.py`
- `scripts/run_partition_management_cron.sh` — cron wrapper

---

## PR-03 — A/B-фреймворк: sticky distribution + sequential testing

> **Компонент ROADMAP §5.2.3** | **Артефакт**: `app/services/ab_testing.py`, `experiment_id` в `SearchRequest`, daily metrics aggregation

### Context

A/B-фреймворк позволяет управлять качеством поиска через данные: тестировать fusion-стратегии (RRF vs weighted vs weighted+rerank), параметры (`α`, `λ`, `K`), и новые фичи (персонализация, MMR) на реальном трафике. Ключевое требование — sticky distribution: один пользователь всегда видит один вариант (консистентное хеширование по `user_id`), иначе метрики загрязняются переключениями. Sequential testing позволяет останавливать эксперимент раньше, чем fixed-horizon, без inflation of false positives. `experiment_id` пробрасывается из запроса в `search_events` (PR-02), что позволяет агрегировать метрики (CTR, MRR, доля no-click, latency p95) per-variant daily.

### Goal

1. Реализовать `app/services/ab_testing.py::ABTestingService`:
   - `def assign_variant(self, user_id: UUID, experiment_id: str, variants: list[str], weights: list[float] | None = None) -> str` — sticky distribution через консистентное хеширование `hash(user_id + experiment_id) % buckets` → маппинг на variant. Deterministic: один `user_id` + `experiment_id` → всегда один variant.
   - `async def create_experiment(self, session, experiment_id: str, description: str, variants: list[str], weights: list[float], start_date: date) -> None` — регистрация эксперимента в БД (таблица `ab_experiments`).
   - `async def get_active_experiments(self, session) -> list[Experiment]` — список активных экспериментов.
   - `async def compute_daily_metrics(self, session, experiment_id: str, date: date) -> dict[str, dict[str, float]]` — агрегирует метрики per-variant из `search_events`: CTR, MRR, доля no-click, latency p95, count.
   - `async def check_significance(self, session, experiment_id: str) -> dict[str, Any]` — sequential testing: вычисляет p-value / confidence bounds, возвращает `{"significant": bool, "winning_variant": str | None, "p_value": float, "samples_per_variant": dict}`.
   - `async def stop_experiment(self, session, experiment_id: str, winning_variant: str) -> None` — помечает эксперимент как завершённый.
2. Создать Alembic-миграцию `008_ab_experiments` (или расширить `007`): таблица `ab_experiments` (`id SERIAL PK`, `experiment_id TEXT UNIQUE`, `description TEXT`, `variants JSONB`, `weights JSONB`, `status TEXT DEFAULT 'active'`, `start_date DATE`, `end_date DATE`, `winning_variant TEXT`, `created_at TIMESTAMPTZ`).
3. Интегрировать в `SearchOrchestrator`: при `req.experiment_id is not None` и `req.user_id is not None` — вызвать `ABTestingService.assign_variant()`, применить variant-специфичные параметры (например, `fusion="weighted"`, `fusion_alpha=0.7` для variant B). Записать `variant` в `search_events` (поле `variant` — добавлено в PR-08, или использовать `experiment_id` с суффиксом).
4. Добавить endpoint `POST /experiments` (создание эксперимента), `GET /experiments/{id}/metrics` (daily metrics), `POST /experiments/{id}/stop` (завершение).
5. Реализовать daily cron `scripts/run_ab_metrics.py` — для каждого активного эксперимента вычисляет метрики, проверяет significance, логирует результат, обновляет `ab_experiments.status` если significant.

### Constraints

- Sticky distribution: `hash(f"{user_id}:{experiment_id}") % sticky_hash_buckets` → маппинг на variant по кумулятивным весам. Deterministic, stateless (не требует хранения assignment в БД — вычисляется on-the-fly).
- Веса: по умолчанию равные (`[0.5, 0.5]` для 2 variants). Настраиваемые per-experiment.
- Sequential testing: использовать always-valid p-values (mSPRT — mixture Sequential Probability Ratio Test) или alpha-spending function. Не использовать naive t-test с repeated peeking (inflation of false positives).
- Минимум `min_samples_per_variant` (100) samples перед проверкой significance — иначе статзначимость не вычисляется.
- Метрики per-variant:
  - **CTR** (Click-Through Rate): `clicks / searches` — доля запросов, завершившихся кликом.
  - **MRR** (Mean Reciprocal Rank): `mean(1 / position_of_clicked_doc)`.
  - **No-click rate**: `searches_without_click / total_searches`.
  - **Latency p95**: 95-й перцентиль `latency_ms` из `search_events`.
  - **Recall@10** (опционально, если есть judged queries): из nightly eval.
- `experiment_id` в `SearchRequest` — опциональный, `str`. Если `None` — эксперимент не активен.
- Variant-параметры: маппинг `variant → {fusion, fusion_alpha, rerank, personalize, diversify}` — хранится в `ab_experiments.variants` JSONB.
- `assign_variant` — синхронная функция (pure computation, no I/O), O(1).
- Метрики: `ab_experiments_active` (Gauge), `ab_assignments_total` (Counter, labels `experiment_id`, `variant`), `ab_significance_checked_total` (Counter, label `result`).
- Max parallel experiments: `ABTestingConfig.max_parallel_experiments` (3). При превышении — `create_experiment` возвращает `409 Conflict`.

### References

- **ARCHITECT §11.3** — A/B-тестирование (sticky distribution, sequential testing, метрики).
- **ARCHITECT §11.4** — логирование запросов (experiment_id, variant).
- **ROADMAP §5.2.3** — параметры: sticky по `user_id`, sequential testing, daily metrics.
- **BACKLOG.md §3.1 PR-03** — зависимости (PR-02), артефакты.
- **ROADMAP §8.1** — зависимость PR-03 → Improvements IM-13 (multi-armed bandit для α).

### Acceptance Criteria

- `ABTestingService.assign_variant(user_id, experiment_id, ["A", "B"])` — deterministic: один `user_id` + `experiment_id` → всегда один variant. Проверить 100 вызовов — одинаковый результат.
- Распределение: 10000 случайных `user_id` → ~50/50 для равных весов, ~70/30 для весов `[0.7, 0.3]`.
- `POST /search` с `experiment_id="exp1"`, `user_id=<uuid>` — в `search_events` записывается `experiment_id="exp1"` и `variant="A"` (или `"B"`).
- `compute_daily_metrics()` — возвращает `{"A": {"ctr": 0.12, "mrr": 0.45, "no_click_rate": 0.3, "latency_p95": 180, "count": 1500}, "B": {...}}`.
- `check_significance()` — после `min_samples_per_variant` возвращает `{"significant": True/False, "winning_variant": "B" or None, "p_value": ...}`.
- `POST /experiments` — создаёт эксперимент, возвращает `201`.
- `GET /experiments/{id}/metrics` — возвращает daily metrics.
- `POST /experiments/{id}/stop` — помечает `status='stopped'`, `winning_variant='B'`, `end_date=today`.
- `scripts/run_ab_metrics.py` — для каждого активного эксперимента вычисляет метрики, логирует, обновляет статус.
- Метрики `ab_experiments_active`, `ab_assignments_total` — экспонируются.
- `POST /search` без `experiment_id` — работает идентично Critical.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_ab_testing_test.py`, `pytest tests/integration/test_ab_experiment_test.py` — зелёные.

### Forbidden patterns

- Не хранить assignment в БД — sticky distribution вычисляется детерминистически on-the-fly (stateless).
- Не использовать naive t-test с repeated peeking — только sequential testing (mSPRT или alpha-spending).
- Не запускать эксперимент с `min_samples_per_variant` < 100 — ранняя проверка significance возвращает `insufficient_samples`.
- Не превышать `max_parallel_experiments` — `create_experiment` возвращает `409`.
- Не применять variant-параметры для анонимных запросов (`user_id is None`) — sticky distribution требует `user_id`.
- Не забывать логировать `variant` в `search_events` — без этого метрики не посчитаются.

### Suggested files

- `app/services/ab_testing.py` — `ABTestingService` (заменить заготовку из PR-00)
- `alembic/versions/008_ab_experiments.py` — миграция
- `app/db/models/ab_experiment.py` — SQLAlchemy модель
- `app/db/queries/ab_experiments.py` — CRUD
- `app/api/routes/experiments.py` — endpoints
- `app/api/deps.py` — `get_ab_testing_service()` factory
- `app/search/orchestrator.py` — интеграция `assign_variant`
- `scripts/run_ab_metrics.py` — daily cron
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_ab_testing_test.py`
- `tests/integration/test_ab_experiment_test.py`

---

## PR-04 — MMR anti-bubble diversification (λ=0.7, adaptive)

> **Компонент ROADMAP §5.2.4** | **Артефакт**: `app/search/diversify.py`, `diversify=true` в `SearchRequest`

### Context

Чистая персонализация (PR-01) ведёт к «пузырю» — пользователь видит только то, что уже знает, и не обнаруживает новый контент. MMR (Maximal Marginal Relevance) решает эту проблему: на финальном шаге ранжирования каждый следующий документ выбирается с учётом не только релевантности запросу, но и различия с уже отобранными документами. Формула: `MMR(d) = λ * relevance(d, q) - (1-λ) * max_{d' ∈ S} similarity(d, d')`, где `S` — уже отобранные документы, `λ=0.7` default (баланс релевантность/разнообразие). Гарантирует, что в top-10 будет хотя бы 2–3 документа из других категорий. Адаптивный `λ`: при падении `diversity_score` топ-10 система автоматически увеличивает `1-λ` (усиливает разнообразие).

### Goal

1. Реализовать `app/search/diversify.py::mmr_diversify`:
   - `def mmr_diversify(candidates: list[tuple[UUID, float]], doc_vectors: dict[UUID, np.ndarray], lambda_param: float = 0.7, top_k: int = 20, lambda_adaptive: bool = True) -> list[tuple[UUID, float]]` — алгоритм MMR:
     1. Отсортировать `candidates` по `relevance` (score) убыванию.
     2. Добавить топ-1 в результат `S`.
     3. Для каждого следующего: вычислить `MMR(d) = λ * rel(d) - (1-λ) * max_{d' ∈ S} cosine(d, d')`, выбрать документ с максимальным MMR, добавить в `S`.
     4. Повторять до `top_k`.
     5. Adaptive `λ`: после формирования top-10 вычислить `diversity_score = mean(1 - max_sim)` — если < 0.3, увеличить `1-λ` на 0.1, переформировать.
2. Интегрировать в `SearchOrchestrator`: при `req.diversify=True` и `feature_flags.diversify_enabled` — после fusion (и опционально после rerank) применить `mmr_diversify`. В `debug` добавить `diversified=True/False`, `lambda_param`, `diversity_score`.
3. `diversify=true` работает как с персонализацией (`personalize=true`), так и без (pure fusion).

### Constraints

- `λ=0.7` default (ARCHITECT §10.5): 70% релевантность, 30% разнообразие. Настраивается через `SearchRequest.diversify_lambda: float | None = None` (если None — использовать дефолт 0.7).
- Doc vectors: загружаются через `EmbeddingService.embed_texts` (кэш через content-hash) для топ-K кандидатов. Cosine similarity = dot product (векторы нормализованы).
- Adaptive `λ`: после формирования top-K вычисляется `diversity_score = mean(1 - pairwise_max_sim)`. Если < 0.3 — `1-λ` увеличивается на 0.1 (макс `1-λ = 0.5`, т.е. `λ = 0.5`), top-K переформируется. Если `diversity_score >= 0.3` — `λ` остаётся 0.7.
- Complexity: MMR — O(K² * D) где K = top_k, D = vector dimension (1024). Для K=20 — 400 dot products, ~1ms на numpy. Приемлемо.
- Гарантия (ARCHITECT §10.5): в top-10 хотя бы 2–3 документа из других категорий, чем последние клики пользователя (если `recent_categories` доступны). Это soft constraint — проверяется метрикой, не hard filter.
- Если `len(candidates) <= 1` — возвращать как есть (нет смысла diversify).
- Если `doc_vectors` не содержит вектор для какого-то `doc_id` — пропустить документ (логировать warning).
- `diversify=true` + `rerank=true`: MMR применяется после rerank (на финальном списке).
- `diversify=true` + `personalize=true`: MMR применяется после personalize_scores.
- Метрики: `mmr_diversify_total` (Counter, label `adaptive` — "true"/"false"), `mmr_diversity_score` (Gauge — последний diversity_score), `mmr_lambda_param` (Gauge — последний использованный λ).

### References

- **ARCHITECT §10.5** — Anti-bubble diversification (MMR формула, λ, adaptive, гарантия 2–3 документа).
- **ARCHITECT §7.2** — Weighted fusion (MMR работает поверх fusion scores).
- **ROADMAP §5.2.4** — параметры: `λ=0.7`, `diversify=true` в API, adaptive `λ`.
- **ROADMAP §10.3 TRIZ §13** — «Сделай наоборот»: MMR компенсирует «пузырь фильтров» от персонализации.
- **BACKLOG.md §3.1 PR-04** — зависимости (PR-01 мягкая), артефакты.

### Acceptance Criteria

- `mmr_diversify(candidates, doc_vectors, lambda_param=0.7, top_k=10)` — возвращает 10 документов, отсортированных по MMR.
- diversity_score top-10 ≥ 0.3 (при adaptive λ). Без adaptive — может быть ниже.
- Adaptive λ: если diversity_score < 0.3 при λ=0.7 — λ уменьшается до 0.6, diversity_score растёт.
- `POST /search` с `diversify=true` — response hits diversify-отсортированы, `debug.diversified=True`, `debug.diversity_score=...`.
- `POST /search` с `diversify=true, personalize=true, rerank=true` — MMR применяется последним (после rerank + personalize).
- `POST /search` без `diversify` — работает идентично Critical.
- В top-10 при `diversify=true` — хотя бы 2 документа из разных категорий (если `attributes.category` доступен — проверить через тест).
- Метрики `mmr_diversify_total`, `mmr_diversity_score` — экспонируются.
- Latency overhead MMR: ≤ 5ms для top-20 (numpy vectorized).
- `ruff check`, `mypy app/`, `pytest tests/unit/test_mmr_diversify_test.py`, `pytest tests/integration/test_mmr_e2e_test.py` — зелёные.

### Forbidden patterns

- Не применять MMR до fusion — только после (на финальном списке).
- Не использовать brute-force O(K³) — только O(K²) с numpy vectorized dot products.
- Не блокировать search response на загрузку doc_vectors — кэш должен срабатывать (content-hash).
- Не забывать adaptive λ — без него MMR статичен и не реагирует на «пузырь».
- Не применять MMR при `len(candidates) <= 1` — нет смысла.
- Не дублировать `EmbeddingService` — использовать существующий инстанс.

### Suggested files

- `app/search/diversify.py` — `mmr_diversify` (заменить заготовку из PR-00)
- `app/search/orchestrator.py` — интеграция MMR после fusion/rerank/personalize
- `app/api/schemas.py` — добавить `diversify_lambda: float | None = None` в `SearchRequest`
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_mmr_diversify_test.py`
- `tests/integration/test_mmr_e2e_test.py`

---

## PR-05 — Контекстная персонализация: device / session / time_of_day

> **Компонент ROADMAP §5.2.5** | **Артефакт**: `SearchContext` интеграция, multiple profile vectors, выбор профиля по контексту

### Context

Пользователь ведёт себя по-разному в зависимости от контекста: утром на desktop ищет рабочие документы, вечером на mobile — развлекательный контент. Единый `profile_vector` (из PR-01) усредняет эти сценарии и теряет сигнал. Контекстная персонализация решает это: `profile_vector` может быть разный для разных контекстов — time-of-day profile, device profile, session profile (короткоживущий, по последним N кликам в сессии, `α=0.5` для быстрого обучения). `SearchContext {device, session_id, time_of_day}` передаётся в запросе, и профиль выбирается по контексту. Это расширяет PR-01, не заменяя — если контекст не передан, используется default `profile_vector`.

### Goal

1. Расширить таблицу `users` (миграция `009_user_context_profiles`): добавить JSONB-колонку `context_profiles` для хранения нескольких профилей: `{"morning": VECTOR, "evening": VECTOR, "desktop": VECTOR, "mobile": VECTOR, "session:abc123": VECTOR}`. Векторы хранятся как JSON arrays (pgvector не поддерживает JSONB VECTOR, поэтому — base64-encoded float32 arrays или отдельная таблица `user_context_profiles`).
2. Альтернатива (предпочтительная): создать таблицу `user_context_profiles` (`user_id UUID FK`, `context_key TEXT` (например `"morning"`, `"desktop"`, `"session:abc123"`), `profile_vector VECTOR(1024)`, `click_count INT`, `profile_version INT`, `updated_at TIMESTAMPTZ`, PK `(user_id, context_key)`). Это позволяет использовать pgvector indexes.
3. Реализовать `app/services/personalization.py::ContextualPersonalizationService` (расширение `PersonalizationService`):
   - `async def get_contextual_profile_vector(self, user_id: UUID, tenant_id: UUID, context: SearchContext) -> np.ndarray | None` — выбирает профиль по контексту: если `context.session_id` — session profile; если `context.time_of_day` — time-of-day profile; если `context.device` — device profile. Если контекст None — default `profile_vector`. Fallback: если контекстный профиль cold-start (click_count=0) — fallback на default profile.
   - `async def update_on_click_contextual(self, user_id, tenant_id, clicked_doc_id, embedding_service, context: SearchContext) -> None` — EMA-обновление контекстного профиля. Session profile: `α=0.5` (быстрое обучение), `click_count` сбрасывается при новой сессии.
   - `async def expire_session_profiles(self, session_ttl_hours: int = 24) -> None` — cron-воркер: удаляет session profiles старше `session_ttl_hours`.
4. Интегрировать в `SearchOrchestrator`: при `req.personalize=True` и `req.context is not None` — использовать `ContextualPersonalizationService.get_contextual_profile_vector()` вместо default. В `debug` добавить `context_profile_key` (например `"morning"`, `"session:abc123"`).

### Constraints

- `SearchContext` поля (из PR-00): `device`, `session_id`, `time_of_day`. Все опциональные. Приоритет: `session_id` > `time_of_day` > `device` > default.
- Session profile: `α=0.5` (быстрое обучение, короткоживущий). TTL: 24 часа (настраивается). Session ID — клиентский (UUID или opaque string).
- Time-of-day: 4 профиля — `morning` (6-12), `afternoon` (12-18), `evening` (18-24), `night` (0-6). UTC или tenant timezone? — UTC (упрощение), настраивается.
- Device: 3 профиля — `desktop`, `mobile`, `tablet`.
- Cold-start для контекстного профиля: если `context_profile.click_count == 0` — fallback на default `profile_vector` (из PR-01). Если и default cold-start — pure fusion.
- `context_profiles_enabled` feature flag — если False, `SearchContext` игнорируется, используется default profile.
- EMA для контекстных профилей: `α=0.1` для time-of-day/device (медленная адаптация, как default), `α=0.5` для session (быстрая).
- GDPR: `gdpr_delete` (из PR-01) должен удалять и `user_context_profiles` (cascade или explicit DELETE).
- Метрики: `contextual_profile_used_total` (Counter, label `context_key` — "morning"/"desktop"/"session"), `session_profiles_expired_total` (Counter).

### References

- **ARCHITECT §10.6** — Контекстная персонализация (device/time/session, multiple profiles).
- **ARCHITECT §10.1** — User profile vector (EMA, α — базовый механизм).
- **ROADMAP §5.2.5** — параметры: `SearchContext {device, session_id, time_of_day}`, multiple profile vectors, выбор по контексту.
- **ROADMAP §10.3 TRIZ §17** — «Переход в другое измерение»: профиль выбирается по device/time/session.
- **BACKLOG.md §3.1 PR-05** — зависимости (PR-01), артефакты.

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу `user_context_profiles` (или расширяет `users`).
- `ContextualPersonalizationService.get_contextual_profile_vector(user, context={time_of_day: "morning"})` — возвращает morning profile или fallback на default.
- Session profile: `update_on_click_contextual` с `context={session_id: "abc"}` — создаёт/обновляет session profile с `α=0.5`.
- Session profile TTL: `expire_session_profiles(24)` — удаляет session profiles старше 24 часов.
- `POST /search` с `personalize=true, context={device: "mobile"}` — использует mobile profile, `debug.context_profile_key="mobile"`.
- `POST /search` с `personalize=true, context={session_id: "xyz"}` — использует session profile, `debug.context_profile_key="session:xyz"`.
- `POST /search` с `personalize=true, context=None` — использует default profile (из PR-01).
- Cold-start контекстного профиля: `context_profile.click_count=0` → fallback на default profile, `debug.context_profile_key="default"` (fallback).
- GDPR delete: `DELETE /users/{user_id}` удаляет и `user_context_profiles` (cascade или explicit).
- `feature_flags.context_personalization_enabled=False` → `SearchContext` игнорируется.
- Метрики `contextual_profile_used_total`, `session_profiles_expired_total` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_context_personalization_test.py` — зелёные.

### Forbidden patterns

- Не создавать отдельный `profile_vector` колонку для каждого контекста в `users` — это не масштабируется. Использовать отдельную таблицу `user_context_profiles` с `(user_id, context_key)` PK.
- Не хранить session profiles бесконечно — TTL 24 часа, cron-чистка.
- Не использовать tenant timezone для time-of-day — UTC (упрощение, настраивается через config).
- Не забывать fallback на default profile при cold-start контекстного профиля.
- Не применять контекстную персонализацию, если `feature_flags.context_personalization_enabled=False`.

### Suggested files

- `alembic/versions/009_user_context_profiles.py` — миграция
- `app/db/models/user_context_profile.py` — SQLAlchemy модель
- `app/db/queries/user_context_profiles.py` — CRUD
- `app/services/personalization.py` — расширение `ContextualPersonalizationService` (или отдельный класс)
- `app/search/orchestrator.py` — интеграция контекстного профиля
- `scripts/expire_session_profiles.py` — cron-воркер
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_context_personalization_test.py`

---

## PR-06 — Distributed tracing: OpenTelemetry SDK → Tempo, trace↔log correlation

> **Компонент ROADMAP §5.2.6** | **Артефакт**: `app/observability/tracing.py`, OTel SDK instrumentation, Tempo export, trace_id correlation с Loki

### Context

Critical-фаза (C-12) добавила метрики (Prometheus) и structured logging (structlog с `trace_id` placeholder). Но `trace_id` в логах — это `request_id` (из `request_id_ctx`), не реальный distributed trace ID. Production-Ready добавляет третье измерение observability — distributed traces через OpenTelemetry SDK, экспортируемые в Tempo. Span `search.request` с атрибутами (`query_lang`, `fusion`, `experiment_id`, `user_id_hashed`) и дочерние spans (`pg.lexical`, `qdrant.vector`, `redis.cache_lookup`, `rerank`, `personalize`, `pg.facets`) дают end-to-end видимость: можно открыть trace в Tempo по `trace_id` из лога Loki, и наоборот. Это критично для debugging latency regressions в production.

### Goal

1. Реализовать `app/observability/tracing.py`:
   - `def setup_tracing(app: FastAPI, config: TracingConfig) -> None` — инициализация OTel SDK: `TracerProvider`, `BatchSpanProcessor`, `OTLPSpanExporter` (gRPC → `config.otel_endpoint`). Регистрация instrumentations: `FastAPIInstrumentor`, `SQLAlchemyInstrumentor`, `RedisInstrumentor`.
   - `def get_tracer(name: str) -> trace.Tracer` — возвращает tracer для создания spans.
   - `@contextmanager def span(tracer: trace.Tracer, name: str, attributes: dict | None = None)` — context manager для создания child span.
2. Инструментировать `SearchOrchestrator`: создать span `search.request` с атрибутами `query_lang`, `fusion`, `experiment_id`, `user_id_hashed` (SHA256 hash, не raw), `tenant_id`, `rerank`, `personalize`, `diversify`. Дочерние spans: `pg.lexical`, `qdrant.vector`, `redis.cache_lookup`, `fusion`, `rerank`, `personalize`, `pg.facets`.
3. Инструментировать `EmbeddingService.embed_query` — span `embedding.query` с атрибутами `cache_hit`, `model_name`.
4. Инструментировать `RerankerService.rerank` — span `rerank` с атрибутами `n_docs`, `device`, `mock_mode`, `inference_ms`.
5. Обновить `app/observability/logging.py`: `trace_id_ctx` теперь заполняется из OTel span context (`span.get_span_context().trace_id`), а не из `request_id`. `request_id` остаётся отдельным полем (для backward compat). Формат `trace_id`: 32-char hex string (OTel format).
6. Обновить `app/api/middleware/request_id.py`: если OTel active — `trace_id_ctx.set(hex(span_context.trace_id))`.
7. Настроить `docker-compose.yml`: `otel-collector` (receives OTLP → exports to Tempo + Loki), `tempo` (trace storage), `loki` (log storage). Grafana datasource для Tempo + Loki.
8. Настроить `monitoring/otel-collector-config.yaml`: receivers (otlp grpc :4317), processors (batch), exporters (otlp/tempo, loki).

### Constraints

- OTel SDK: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`. Instrumentations: `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-redis`.
- Sampling: `TracingConfig.sample_rate` — `ParentBased(TraceIdRatioBased(sample_rate))`. В dev: 1.0 (100%), в prod: 0.1 (10%) — настраивается через env `OTEL_TRACES_SAMPLER_ARG`.
- Export: `BatchSpanProcessor` с `OTLPSpanExporter(endpoint=config.otel_endpoint, insecure=True)`. `export_interval_ms=5000` (batch flush каждые 5 сек).
- Span `search.request` attributes (ARCHITECT §11.2): `query_lang`, `fusion`, `experiment_id`, `user_id_hashed` (SHA256(user_id), не raw — privacy), `tenant_id`, `rerank` (bool), `personalize` (bool), `diversify` (bool), `degraded` (bool), `latency_ms`.
- Дочерние spans: `pg.lexical` (latency, k), `qdrant.vector` (latency, k, pushdown), `redis.cache_lookup` (hit/miss), `fusion` (strategy, alpha), `rerank` (n_docs, device, inference_ms), `personalize` (cold_start, profile_version), `pg.facets` (n_fields, latency).
- `trace_id` correlation: structlog `trace_id_ctx` = OTel `span.get_span_context().trace_id` (как hex string). В логах Loki поле `trace_id` — кликабельная ссылка на Tempo trace (Grafana datasource link).
- `request_id` остаётся отдельным полем (для backward compat с MVP/Critical logging). Связь: `request_id` → span attribute `request_id` (можно фильтровать traces по `request_id`).
- Performance: OTel instrumentation overhead ≤ 2ms per span (batch processor, async export). Не блокирует search path.
- `feature_flags.tracing_enabled=False` → instrumentation отключается, spans не создаются (no-op tracer).
- `setup_tracing` вызывается в FastAPI lifespan (startup).
- Метрики: `otel_spans_exported_total` (Counter), `otel_export_errors_total` (Counter).

### References

- **ARCHITECT §11.2** — Distributed tracing (spans, attributes, export в Tempo, trace↔log correlation).
- **ARCHITECT §11.4** — Логирование (trace_id correlation).
- **ARCHITECT §11.1** — Метрики (три измерения observability: metrics ↔ logs ↔ traces).
- **ROADMAP §5.2.6** — параметры: OTel SDK, spans, Tempo, trace_id correlation.
- **ROADMAP §10.3 TRIZ §17** — «Переход в другое измерение»: metrics ↔ logs ↔ traces коррелируют по `trace_id`.
- **BACKLOG.md §3.1 PR-06** — зависимости (C-12), артефакты.

### Acceptance Criteria

- `setup_tracing(app, config)` — инициализирует OTel SDK, FastAPIInstrumentor регистрируется.
- `POST /search` — создаёт span `search.request` с дочерними spans. Проверить: в Tempo (http://localhost:3200/api/search) — trace с spans.
- Span attributes: `query_lang`, `fusion`, `experiment_id` (если есть), `user_id_hashed` (SHA256), `tenant_id`, `rerank`, `personalize`, `diversify`, `degraded`, `latency_ms`.
- `trace_id` в structlog log = OTel span trace_id (hex string). Проверить: `grep trace_id` в логах → клик в Tempo → открывает trace.
- `request_id` остаётся в логах (backward compat).
- Sampling: `sample_rate=1.0` (dev) → все traces экспортируются. `sample_rate=0.1` (prod) → 10%.
- `feature_flags.tracing_enabled=False` → spans не создаются (no-op), логирование работает как в Critical.
- OTel collector: `docker-compose up otel-collector` — healthy. Tempo: `docker-compose up tempo` — healthy. Loki: healthy.
- Grafana: datasource Tempo + Loki настроены, trace view работает (Explore → Tempo → trace by trace_id).
- Performance overhead: p99 `/search` latency не деградировала (≤ 120ms без rerank, ≤ 350ms с rerank). Regression test зелёный.
- Метрики `otel_spans_exported_total`, `otel_export_errors_total` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_tracing_test.py` — зелёные.

### Forbidden patterns

- Не хранить raw `user_id` в span attributes — только SHA256 hash (privacy).
- Не использовать `AlwaysOnSampler` в prod — только `TraceIdRatioBased` с `sample_rate` (perf).
- Не блокировать search path на span creation — OTel SDK async (BatchSpanProcessor).
- Не создавать spans для каждого SQL query (слишком шумно) — SQLAlchemyInstrumentor с `set_statement_preparer` (sanitize SQL, или отключить query capture).
- Не забывать `trace_id` correlation в логах — иначе traces и logs не связываются.
- Не дублировать `request_id` и `trace_id` — `trace_id` = OTel, `request_id` = middleware (оба в логах, но `trace_id` кликабельный в Tempo).

### Suggested files

- `app/observability/tracing.py` — `setup_tracing`, `get_tracer`, `span` context manager (заменить заготовку из PR-00)
- `app/observability/logging.py` — обновить `trace_id_ctx` из OTel span context
- `app/api/middleware/request_id.py` — интеграция OTel trace_id
- `app/search/orchestrator.py` — добавить spans для каждого этапа
- `app/embedding/service.py` — span `embedding.query`
- `app/reranker/service.py` — span `rerank`
- `app/main.py` — вызвать `setup_tracing` в lifespan
- `docker-compose.yml` — otel-collector, tempo, loki сервисы
- `monitoring/otel-collector-config.yaml` — receivers, processors, exporters
- `grafana/provisioning/datasources/datasources.yml` — добавить Tempo, Loki datasources
- `grafana/provisioning/dashboards/prod-tracing.json` — dashboard
- `tests/unit/test_tracing_test.py`

---

## PR-07 — Миграционный фреймворк: blue-green, backfill, канарейка, snapshot, rollback

> **Компонент ROADMAP §5.2.7** | **Артефакт**: `app/services/migration.py`, `app/migration/` пакет, CLI `python -m app.migration`

### Context

Смена embedding-модели (например, bge-m3 → bge-m3-v2, или bge-m3 → LaBSE) — самая рискованная операция в Production-Ready: неправильная миграция может дать регрессию качества (Recall@10 падает на 10%+) или потребовать часы downtime. Архитектурное решение — zero-downtime миграция через blue-green коллекции в Qdrant: создаём новую коллекцию `documents_v2`, backfill-воркер пересчитывает embeddings батчами (50k doc/hour на GPU), канареечный reindex на 1% проверяет semantic diff (cosine sim старой и новой модели), snapshot-on-reindex позволяет откат за минуты. После недели stable — старая коллекция удаляется. Этот фреймворк также покрывает сценарий полного восстановления после потери Qdrant (full reindex из Postgres).

### Goal

1. Реализовать `app/services/migration.py::MigrationService`:
   - `async def init_migration(self, new_model_name: str, new_dimension: int) -> str` — шаг 1: создаёт запись в `embedding_models` с `is_default=false`, создаёт новую Qdrant коллекцию `documents_v2` (или `documents_{model_rev}`). Возвращает `migration_id`.
   - `async def canary_reindex(self, migration_id: str, percentage: float = 0.01) -> CanaryReport` — шаг 2: выбирает 1% случайных документов, вычисляет embeddings новой моделью, сравнивает cosine similarity со старой. Возвращает `CanaryReport {avg_similarity, min_similarity, max_similarity, passed: bool}`. `passed=True` если `min_similarity >= canary_min_similarity` и `avg_similarity <= canary_max_similarity`.
   - `async def snapshot_qdrant(self, collection_name: str) -> str` — шаг 3 (перед full reindex): создаёт snapshot Qdrant коллекции, загружает в S3 (`snapshot_s3_bucket`). Возвращает S3 key.
   - `async def backfill(self, migration_id: str, batch_size: int = 1000) -> BackfillReport` — шаг 4: идёт по `documents` батчами, вычисляет embeddings новой моделью, upsert в `documents_v2`. Скорость ~50k doc/hour. Возвращает `BackfillReport {processed, total, speed_doc_per_hour, estimated_remaining}`.
   - `async def promote(self, migration_id: str) -> None` — шаг 5: переключает `is_default=true` для новой модели, API начинает dual-write в обе коллекции, Search Orchestrator переключается по feature-flag.
   - `async def rollback(self, migration_id: str) -> None` — откат: восстанавливает Qdrant из snapshot (или переключает на старую коллекцию), `is_default=false` для новой модели.
   - `async def cleanup_old_collection(self, migration_id: str) -> None` — шаг 7 (после недели stable): удаляет старую коллекцию, снимает dual-write.
2. Реализовать `app/migration/` пакет:
   - `__main__.py` — CLI: `python -m app.migration init --model bge-m3-v2`, `canary`, `snapshot`, `backfill`, `promote`, `rollback`, `cleanup`, `status`.
   - `backfill_worker.py` — async воркер с batching, rate limiting, progress tracking.
   - `canary_check.py` — логика канареечного reindex + semantic diff.
   - `snapshot.py` — Qdrant snapshot creation + S3 upload (через `boto3`).
3. Реализовать сценарий полного восстановления после потери Qdrant (ARCHITECT §13.3):
   - `async def full_reindex_from_postgres(self) -> BackfillReport` — поднимает пустой Qdrant, создаёт коллекцию, full reindex из `documents` с фильтром `deleted_at IS NULL`. Reconciler накатывает то, что успело протечь.
4. Интегрировать dual-write в `IndexingService`: при `migration.status == 'promoted'` — `upsert_point` вызывается для обеих коллекций (`documents` и `documents_v2`).

### Constraints

- Blue-green: новая коллекция `documents_v2` (или `documents_{model_rev}`). `QdrantPayload.model_name` distinguishes points. Vector search (P-10) фильтрует по `model_name` — migration не ломает существующий search.
- Канареечный reindex (ARCHITECT §13.5): 1% случайных `documents` (по `id % 100 == random_bucket`). Вычислить embeddings обеими моделями, cosine similarity. Thresholds: `canary_min_similarity=0.6` (слишком сильное изменение → стоп), `canary_max_similarity=0.95` (слишком похожа → не тратить ресурсы). `passed=True` если `min_sim >= 0.6` и `avg_sim <= 0.95`.
- Snapshot-on-reindex (ARCHITECT §13.6): Qdrant `create_snapshot` API → загрузка в S3. Retention 30 дней (S3 lifecycle rule). `rollback` восстанавливает из snapshot.
- Backfill: батчи по `MigrationConfig.backfill_batch_size` (1000). Скорость ~50k doc/hour на GPU-ноду. Для 1M документов — ~20 часов (1 нода), ~3 часа (7 нод). Progress tracking: `backfill_progress` Gauge (0.0–1.0).
- Dual-write: после `promote`, `IndexingService` пишет в обе коллекции. Search читает из новой (feature-flag `search_collection='documents_v2'`).
- Rollback: если после promote обнаружена регрессия (nightly eval) — `rollback`: переключить `is_default=false` для новой, `search_collection='documents'` (старая). Или восстановить Qdrant из snapshot если старая коллекция повреждена.
- Cleanup: после 7 дней stable (nightly eval зелёный, A/B на 10% подтверждает) — удалить старую коллекцию, снять dual-write.
- S3: `boto3` async (через `asyncio.to_thread`). Bucket: `MigrationConfig.snapshot_s3_bucket`. Dev: minio (docker-compose).
- CLI: `python -m app.migration init --model "bge-m3-v2" --dimension 1024` → `canary` → `snapshot` → `backfill` → `promote` → (week later) → `cleanup`.
- Метрики: `migration_backfill_progress` (Gauge, label `migration_id`), `migration_backfill_speed` (Gauge, doc/hour), `migration_canary_similarity` (Gauge — avg/min/max), `migration_snapshots_created_total` (Counter), `migration_rollbacks_total` (Counter).
- Reconciler integration: после `promote`, reconciler (P-13) обрабатывает обе коллекции (dual-write).

### References

- **ARCHITECT §13.1** — Сценарий: замена embedding-модели (zero-downtime шаги 1-7).
- **ARCHITECT §13.2** — Сценарий: zero-downtime reindex при изменении payload-индексов.
- **ARCHITECT §13.3** — Сценарий: полное восстановление после потери Qdrant.
- **ARCHITECT §13.4** — Версионирование схем.
- **ARCHITECT §13.5** — Канареечный reindex (semantic-diff, thresholds).
- **ARCHITECT §13.6** — Snapshot-on-reindex (S3, retention, rollback).
- **ROADMAP §5.2.7** — параметры: blue-green, backfill 50k/hour, canary 1%, snapshot, rollback.
- **ROADMAP §10.3 TRIZ §10** — «Предварительное действие»: канареечный reindex перед full.
- **ROADMAP §10.3 TRIZ §11** — «Заранее подложенная подушка»: snapshot-on-reindex.
- **ROADMAP §10.3 TRIZ §26** — «Копирование»: blue-green коллекции.
- **BACKLOG.md §3.1 PR-07** — зависимости (PR-02, C-07), артефакты.

### Acceptance Criteria

- `python -m app.migration init --model "bge-m3-v2" --dimension 1024` — создаёт запись в `embedding_models` (is_default=false), создаёт Qdrant коллекцию `documents_v2`, возвращает `migration_id`.
- `python -m app.migration canary <migration_id>` — выбирает 1% документов, вычисляет embeddings обеими моделями, возвращает `CanaryReport {avg_similarity, min_similarity, max_similarity, passed}`.
- Canary threshold: `min_similarity=0.4` → `passed=False` (стоп миграция). `avg_similarity=0.96` → `passed=False` (модель слишком похожа).
- `python -m app.migration snapshot <migration_id>` — создаёт Qdrant snapshot, загружает в S3/MinIO, возвращает S3 key. Проверить: `aws s3 ls s3://oriahs-qdrant-snapshots/` (minio mc) показывает файл.
- `python -m app.migration backfill <migration_id>` — backfill батчами, progress tracking. Для 1000 документов — завершается за ~1 минуту (mock embeddings) или реальное время.
- `python -m app.migration promote <migration_id>` — `is_default=true` для новой модели, search переключается на `documents_v2`, dual-write включён.
- `python -m app.migration rollback <migration_id>` — восстанавливает из snapshot (или переключает на старую), `is_default=false` для новой.
- `python -m app.migration cleanup <migration_id>` — удаляет старую коллекцию, снимает dual-write.
- `full_reindex_from_postgres()` — поднимает пустой Qdrant, full reindex из `documents` (deleted_at IS NULL), возвращает `BackfillReport`.
- Dual-write: после `promote`, `POST /index` пишет в обе коллекции. Проверить: Qdrant points в `documents` и `documents_v2` для одного `doc_id`.
- Search: после `promote`, `POST /search` читает из `documents_v2` (feature-flag).
- Метрики `migration_backfill_progress`, `migration_canary_similarity`, `migration_rollbacks_total` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_migration_framework_test.py`, `pytest tests/integration/test_migration_canary_test.py` — зелёные.

### Forbidden patterns

- Не переключать `is_default=true` без canary + snapshot — это обязательные pre-conditions.
- Не запускать backfill без canary check (`passed=True`) — иначе регрессия качества.
- Не удалять старую коллекцию сразу после promote — минимум 7 дней stable (nightly eval зелёный).
- Не забыть snapshot перед backfill — иначе rollback невозможен.
- Не блокировать search path на backfill — backfill работает в отдельном воркере.
- Не использовать CPU для backfill — только GPU (bge-m3 inference).
- Не забыть dual-write после promote — иначе новая коллекция отстаёт.
- Не хардкодить collection names — `documents`, `documents_v2` (или `documents_{model_rev}`) через config.

### Suggested files

- `app/services/migration.py` — `MigrationService` (заменить заготовку из PR-00)
- `app/migration/__init__.py`
- `app/migration/__main__.py` — CLI
- `app/migration/backfill_worker.py`
- `app/migration/canary_check.py`
- `app/migration/snapshot.py`
- `app/services/indexing.py` — dual-write интеграция
- `app/search/qdrant_client.py` — поддержка множественных коллекций
- `app/observability/metrics.py` — новые метрики
- `grafana/provisioning/dashboards/prod-migration.json` — dashboard
- `tests/unit/test_migration_framework_test.py`
- `tests/integration/test_migration_canary_test.py`

---

## PR-08 — Shadow traffic: параллельный расчёт новой стратегии

> **Компонент ROADMAP §5.2.8** | **Артефакт**: `app/services/shadow_traffic.py`, `variant` колонка в `search_events`, логирование обоих результатов

### Context

При выкатке новой стратегии (например, переход с RRF на weighted+rerank, или включение персонализации) — не сразу переключаем трафик, потому что метрики могут деградировать незаметно. Shadow traffic решает это: на каждый запрос считаем оба результата (старая стратегия — control, новая — treatment), логируем оба в `search_events` с пометкой `variant="control"` / `variant="treatment"`, но клиенту отдаём только control. Двойная нагрузка обращена в пользу (TRIZ §22): безопасная валидация без риска для пользователя. Онлайн-метрики качества — через interleaved клики на следующей неделе.

### Goal

1. Реализовать `app/services/shadow_traffic.py::ShadowTrafficService`:
   - `async def run_shadow(self, req: SearchRequest, orchestrator: SearchOrchestrator) -> ShadowResult` — параллельно запускает два поиска: control (текущие параметры запроса) и treatment (параметры из `ShadowTrafficConfig.strategies`). Возвращает `ShadowResult {control: SearchResponse, treatment: SearchResponse, latency_ms: int}`.
   - `async def log_shadow(self, shadow_result: ShadowResult, user_id: UUID | None, tenant_id: UUID, experiment_id: str) -> None` — логирует оба результата в `search_events` с `variant="control"` и `variant="treatment"`, `experiment_id` = shadow experiment ID.
2. Создать Alembic-миграцию `009_search_events_variant`: добавить колонку `variant TEXT DEFAULT 'control'` в `search_events` (наследуется партициями).
3. Интегрировать в `SearchOrchestrator`: если `feature_flags.shadow_traffic_enabled` и `ShadowTrafficConfig.enabled` — после основного search запускает `run_shadow` в background task (через `asyncio.create_task`), логирует через `log_shadow`. Клиенту отдаётся только control.
4. Контроль нагрузки: shadow traffic удваивает search latency budget. Использовать `asyncio.gather` для параллельного запуска control + treatment, но с `timeout_ms = req.timeout_ms` (treatment не должен задерживать control).
5. Добавить endpoint `POST /experiments/shadow` — создание shadow experiment (control strategy + treatment strategy), `GET /experiments/shadow/{id}/comparison` — сравнение метрик control vs treatment за период.

### Constraints

- Shadow traffic — только при `feature_flags.shadow_traffic_enabled=True` и `ShadowTrafficConfig.enabled=True`. По умолчанию off — включается per-experiment.
- Параллельность: control и treatment запускаются через `asyncio.gather`. Control имеет приоритет — если treatment падает (timeout/error), control всё равно отдаётся клиенту.
- Логирование: оба результата пишутся в `search_events` с `variant` ("control" / "treatment"), `experiment_id` = shadow experiment ID. `top_doc_ids` для каждого — свой.
- Нагрузка: shadow удваивает QPS на search path. Включать только в non-prod или на 1-10% трафика (через sampling).
- Treatment параметры: `ShadowTrafficConfig.strategies` — список, например `["weighted+rerank"]`. Если несколько treatment — каждый со своим `variant` ("treatment_1", "treatment_2").
- Interleaved клики: на следующем этапе (после недели shadow) — можно включить interleaved mode: клиенту отдаётся смесь control + treatment (interleaved), клики атрибутируются к variant. Это опционально, не в scope PR-08.
- `variant` колонка в `search_events`: `TEXT DEFAULT 'control'`. Для non-shadow запросов — `"control"` (или `NULL` — зафиксировать `"control"`).
- Метрики: `shadow_traffic_total` (Counter, label `treatment_strategy`), `shadow_traffic_errors_total` (Counter, label `reason`), `shadow_traffic_latency_ms` (Histogram — overhead shadow).
- Timeout: `treatment` search имеет `timeout_ms = req.timeout_ms`. Если не уложился — `shadow_traffic_errors_total.inc(reason="timeout")`, control отдаётся.
- Sampling: `ShadowTrafficConfig.sample_rate` (0.0–1.0) — доля запросов, на которых запускается shadow. 1.0 — все запросы, 0.1 — 10%.

### References

- **ARCHITECT §11.6** — Shadow traffic (параллельный расчёт, логирование, interleaved клики).
- **ARCHITECT §11.4** — логирование запросов (variant колонка).
- **ROADMAP §5.2.8** — параметры: shadow traffic, лог в `search_events.variant`.
- **ROADMAP §10.3 TRIZ §22** — «Обратить вред в пользу»: двойная нагрузка → безопасная валидация.
- **BACKLOG.md §3.1 PR-08** — зависимости (PR-03), артефакты.
- **ROADMAP §8.1** — зависимость PR-08 → Improvements (канареечный rollout каскадного fusion).

### Acceptance Criteria

- `alembic upgrade head` добавляет колонку `variant TEXT DEFAULT 'control'` в `search_events` (и все партиции).
- `ShadowTrafficService.run_shadow(req, orchestrator)` — возвращает `ShadowResult` с control и treatment responses.
- При `shadow_traffic_enabled=True`: `POST /search` — клиенту отдаётся control response, в `search_events` появляются 2 строки (`variant="control"` + `variant="treatment"`).
- При `shadow_traffic_enabled=False`: `POST /search` — работает как обычно, 1 строка в `search_events` (`variant="control"`).
- Treatment timeout: если treatment превышает `timeout_ms` — control отдаётся, `shadow_traffic_errors_total.inc(reason="timeout")`.
- Treatment error: если treatment падает (exception) — control отдаётся, error logged.
- `POST /experiments/shadow` — создаёт shadow experiment.
- `GET /experiments/shadow/{id}/comparison` — возвращает `{"control": {ctr, mrr, latency_p95}, "treatment": {...}}`.
- Sampling: `sample_rate=0.1` — только 10% запросов запускают shadow.
- Control latency не деградирует: p99 control ≤ 120ms (без rerank). Shadow overhead — отдельная метрика.
- Метрики `shadow_traffic_total`, `shadow_traffic_errors_total`, `shadow_traffic_latency_ms` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_shadow_traffic_test.py`, `pytest tests/integration/test_shadow_traffic_e2e_test.py` — зелёные.

### Forbidden patterns

- Не отдавать treatment response клиенту — только control (shadow = невидимый).
- Не блокировать control на treatment — `asyncio.gather` с `return_exceptions=True`, control имеет приоритет.
- Не включать shadow в prod без sampling — только non-prod или `sample_rate < 1.0`.
- Не запускать shadow без `experiment_id` — нужен для агрегации метрик.
- Не забывать `variant` колонку в `search_events` — без этого метрики не посчитаются.
- Не запускать treatment с другим `embedding_service` — тот же инстанс (кэш сработает).

### Suggested files

- `app/services/shadow_traffic.py` — `ShadowTrafficService` (заменить заготовку из PR-00)
- `alembic/versions/009_search_events_variant.py` — миграция (variant колонка)
- `app/search/orchestrator.py` — интеграция shadow в background task
- `app/api/routes/experiments.py` — shadow experiment endpoints
- `app/api/schemas.py` — `ShadowExperimentRequest`, `ShadowComparisonResponse`
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_shadow_traffic_test.py`
- `tests/integration/test_shadow_traffic_e2e_test.py`

---

## PR-09 — Chaos engineering weekly: kill Qdrant / delay reranker / fill Redis

> **Компонент ROADMAP §5.2.9** | **Артефакт**: `tests/chaos/*.py`, `scripts/run_chaos_weekly.py`, weekly cron, postcondition-чеки в CI

### Context

Архитектура с safety nets (circuit breaker C-03, degraded mode C-09, reconciler P-13) — это теория, пока не проверена в реальных условиях сбоя. Chaos engineering — плановые инъекции сбоев в non-prod окружении, которые выявляют слабые места до real incident. Каждое воскресенье 06:00 UTC в staging: убить Qdrant на 60 секунд (проверить lex-only fallback без 5xx), задержать Reranker на 1 секунду (проверить circuit breaker open), забить Redis cache (проверить graceful degradation hit rate). Каждый сценарий имеет postcondition-чек в CI; провал блокирует релиз.

### Goal

1. Реализовать `tests/chaos/` пакет с тремя сценариями:
   - `test_kill_qdrant.py` — убивает Qdrant контейнер на `ChaosConfig.qdrant_kill_seconds` (60 сек), запускает `POST /search` в течение этого окна, проверяет: response `200` с `degraded=True`, `total_vector=0`, без 5xx. После восстановления Qdrant — `POST /search` с `degraded=False`.
   - `test_delay_reranker.py` — инжектирует задержку в `RerankerService.rerank` (через mock/config `RerankerConfig.mock_delay_ms=1000`), запускает `POST /search?rerank=true`, проверяет: circuit breaker открывается (`circuit_breaker_state=2`), последующие запросы возвращают `rerank_degraded=True`. После cooldown — half_open → closed.
   - `test_fill_redis_cache.py` — заполняет Redis cache до `ChaosConfig.redis_fill_percentage` (95%), запускает `POST /search`, проверяет: `embedding_cache_hit_rate` деградирует gracefully (не 0%), search не падает.
2. Реализовать `scripts/run_chaos_weekly.py` — orchestrator: запускает все сценарии последовательно, собирает postcondition-чеки, генерирует отчёт, обновляет метрики. Exit 0 если все зелёные, exit 1 если какой-то провален.
3. Настроить weekly cron: `0 6 * * 0` (воскресенье 06:00 UTC) в non-prod.
4. Каждый postcondition-чек — pytest test с маркером `@pytest.mark.chaos`. CI gate: `pytest -m chaos` должен быть зелёным для release.

### Constraints

- Окружение: `ChaosConfig.environment = "staging"` — только non-prod! Production запуск блокируется guard: `if config.environment == "production": raise RuntimeError("Chaos in production forbidden")`.
- Сценарии запускаются последовательно (не параллельно) — каждый сценарий восстанавливает систему перед следующим.
- Kill Qdrant: `docker stop qdrant` через `subprocess`, ждёт 60 сек, `docker start qdrant`. Между stop/start — `POST /search` запросы.
- Delay reranker: через `RerankerConfig` injection (mock mode с delay) или `time.sleep` в mock. Не трогает реальный reranker (production-safe).
- Fill Redis cache: `redis-cli SET` bulk key-value pairs до 95% `used_memory/maxmemory`. Или `DEBUG SET-ACTIVE-EXPIRE 0` + bulk SET.
- Postcondition-чеки:
  - Kill Qdrant: `response.status_code == 200`, `response.json()["degraded"] == True`, `response.json()["total_vector"] == 0`. После восстановления: `degraded == False`.
  - Delay reranker: `circuit_breaker_state == 2` (open) после N запросов. `rerank_degraded == True`. После cooldown: `circuit_breaker_state in [0, 1]`.
  - Fill Redis: `embedding_cache_hit_rate > 0.0` (не 0 — кэш работает даже под давлением). `search_latency_ms` в SLO.
- OTel instrumentation (PR-06) — postcondition-чеки используют traces для верификации (span `search.request` с `degraded=True`).
- Отчёт: JSON-файл `chaos_report_{timestamp}.json` с результатами каждого сценария. Загружается в S3/MinIO для history.
- Метрики: `chaos_scenarios_passed_total` (Counter, label `scenario`), `chaos_scenarios_failed_total` (Counter, label `scenario`), `chaos_last_run_timestamp` (Gauge).
- CI gate: `scripts/run_dod_check_prod.sh` включает `pytest -m chaos` — если fails, release blocked.

### References

- **ARCHITECT §11.7** — Chaos engineering (сценарии, postcondition-чеки, weekly cron).
- **ARCHITECT §11.2** — Distributed tracing (postcondition-чеки через traces).
- **ROADMAP §5.2.9** — параметры: weekly cron 06:00 UTC, non-prod, 3 сценария.
- **ROADMAP §10.3 TRIZ §9** — «Предварительное антидействие»: плановые сбои выявляют слабые места до real incident.
- **BACKLOG.md §3.1 PR-09** — зависимости (PR-06, C-09), артефакты.

### Acceptance Criteria

- `tests/chaos/test_kill_qdrant.py` — запускается, убивает Qdrant, проверяет `degraded=True`, восстанавливает, проверяет `degraded=False`. Pass.
- `tests/chaos/test_delay_reranker.py` — инжектирует delay, проверяет `circuit_breaker_state=2`, `rerank_degraded=True`. Pass.
- `tests/chaos/test_fill_redis_cache.py` — заполняет Redis, проверяет `embedding_cache_hit_rate > 0`. Pass.
- `scripts/run_chaos_weekly.py` — запускает все 3 сценария, генерирует `chaos_report_{timestamp}.json`, exit 0.
- Production guard: `ChaosConfig.environment="production"` → `RuntimeError`, запуск блокируется.
- Weekly cron: `0 6 * * 0` (crontab entry) в non-prod.
- CI gate: `pytest -m chaos` — зелёный для release.
- OTel traces: span `search.request` с `degraded=True` виден в Tempo во время chaos run.
- Метрики `chaos_scenarios_passed_total`, `chaos_last_run_timestamp` — экспонируются.
- `ruff check`, `mypy app/`, `pytest -m chaos` — зелёные.

### Forbidden patterns

- Не запускать chaos в production — guard обязателен (`environment != "production"`).
- Не запускать сценарии параллельно — последовательное выполнение с восстановлением между.
- Не оставлять систему в сломанном состоянии после сценария — teardown обязателен (восстановить Qdrant, сбросить delay, очистить Redis).
- Не использовать chaos без OTel (PR-06) — postcondition-чеки бедные без traces.
- Не блокировать release на chaos без причины — только если postcondition fail.
- Не хардкодить docker commands — через `subprocess` с config-driven container names.

### Suggested files

- `tests/chaos/__init__.py`
- `tests/chaos/conftest.py` — fixtures (chaos orchestrator, docker client)
- `tests/chaos/test_kill_qdrant.py`
- `tests/chaos/test_delay_reranker.py`
- `tests/chaos/test_fill_redis_cache.py`
- `scripts/run_chaos_weekly.py` — orchestrator
- `scripts/run_chaos_weekly_cron.sh` — cron wrapper
- `app/observability/metrics.py` — chaos метрики
- `pyproject.toml` — добавить marker `chaos` в `[tool.pytest.ini_options]`

---

## PR-10 — Adaptive K по `tenant_search_stats`

> **Компонент ROADMAP §5.2.10** | **Артефакт**: миграция `008_tenant_search_stats`, `app/search/adaptive_k.py`, адаптивные `K_lexical`/`K_vector` в рантайме

### Context

MVP использует статические `K_lexical=50` и `K_vector=50` (из `SearchConfig`). Это неоптимально: тенант с 1000 документов не нуждается в K=50 (latency overhead), а тенант с 10M документов — может выиграть от K=100 (больше кандидатов для fusion). Adaptive K решает это: система адаптирует `K` под тенанта на основе `tenant_search_stats` (`doc_count`, `avg_query_len`). Маленькие тенанты (< 10k) → K=30; большие (> 1M) → K=100. Короткие запросы (< 5 токенов) → повышаем `K_lexical` (точные совпадения важнее); длинные (> 20) → повышаем `K_vector` (семантика важнее).

### Goal

1. Создать Alembic-миграцию `010_tenant_search_stats` (или `008` если ещё не занят): таблица `tenant_search_stats` (`tenant_id UUID PK`, `doc_count BIGINT`, `avg_query_len FLOAT`, `total_searches BIGINT`, `last_updated TIMESTAMPTZ`).
2. Реализовать `app/db/queries/tenant_stats.py`:
   - `async def get_or_create(session, tenant_id: UUID) -> TenantSearchStat` — возвращает статистику тенанта (создаёт запись если нет).
   - `async def update_doc_count(session, tenant_id: UUID, count: int) -> None` — обновляет `doc_count`.
   - `async def update_avg_query_len(session, tenant_id: UUID, query_len: int) -> None` — обновляет running average `avg_query_len`.
   - `async def increment_search_count(session, tenant_id: UUID) -> None`.
3. Реализовать `app/search/adaptive_k.py::compute_adaptive_k`:
   - `def compute_adaptive_k(tenant_stats: TenantSearchStat, query: str, config: AdaptiveKConfig) -> tuple[int, int]` — возвращает `(K_lexical, K_vector)` на основе `doc_count`, `avg_query_len`, `len(query)`.
   - Логика:
     - `doc_count < small_tenant_doc_threshold` (10k) → base K = `k_small` (30).
     - `doc_count > large_tenant_doc_threshold` (1M) → base K = `k_large` (100).
     - Иначе → base K = `SearchConfig.k_lexical` (50, default).
     - `len(query) < short_query_len_threshold` (5) → `K_lexical = base + 20`, `K_vector = base - 10` (минимум 10).
     - `len(query) > long_query_len_threshold` (20) → `K_lexical = base - 10`, `K_vector = base + 20`.
4. Интегрировать в `SearchOrchestrator`: если `feature_flags.adaptive_k_enabled` — перед запуском channels загрузить `tenant_search_stats`, вычислить adaptive K, использовать вместо `SearchConfig.k_lexical`/`k_vector`. Обновить `avg_query_len` после поиска.
5. Обновить `tenant_search_stats.doc_count` — cron или on-demand: `scripts/update_tenant_stats.py` — для каждого тенанта `SELECT count(*) FROM documents WHERE tenant_id=... AND deleted_at IS NULL` → update.

### Constraints

- `tenant_search_stats` — одна строка на тенант (PK `tenant_id`). Обновляется: `doc_count` — ежедневно (cron), `avg_query_len` — на каждый search (running average), `total_searches` — инкремент на каждый search.
- Adaptive K вычисляется per-request (синхронно, O(1), не блокирует search).
- `doc_count` — кэшируется в Redis (TTL 1 час) для избежания лишних PG round-trip на каждый search. `tenant_search_stats` таблица обновляется реже, Redis — быстрый read.
- `avg_query_len` — running average: `new_avg = (old_avg * (n-1) + query_len) / n` где `n = total_searches`. Или exponential moving average с `α=0.01`.
- Минимумы: `K_lexical >= 10`, `K_vector >= 10` (даже для самых коротких/длинных запросов).
- Максимумы: `K_lexical <= 200`, `K_vector <= 200` (защита от вырождения).
- Если `tenant_stats is None` (новый тенант) — fallback на `SearchConfig.k_lexical` / `k_vector` (50/50).
- Если `feature_flags.adaptive_k_enabled=False` — используются статические K из config.
- Push-down (C-10) integration: adaptive K влияет и на push-down `max_candidate_ids` — если K_lexical растёт, `max_candidate_ids` тоже растёт пропорционально.
- Метрики: `adaptive_k_used_total` (Counter, label `bucket` — "small"/"medium"/"large"), `adaptive_k_lexical` (Gauge — последний K_lexical), `adaptive_k_vector` (Gauge — последний K_vector).

### References

- **ARCHITECT §6.4** — Адаптивные K (логика по `doc_count`, `avg_query_len`).
- **ARCHITECT §6.1** — Этапы обработки запроса (K параметризация).
- **ROADMAP §5.2.10** — параметры: `tenant_search_stats`, adaptive K_lexical/K_vector.
- **ROADMAP §10.3 TRIZ §23** — «Обратная связь»: adaptive K по tenant stats.
- **BACKLOG.md §3.1 PR-10** — зависимости (PR-02), артефакты.
- **ROADMAP §8.1** — зависимость PR-10 → Improvements IM-03 (tenant tiering).

### Acceptance Criteria

- `alembic upgrade head` создаёт таблицу `tenant_search_stats`.
- `compute_adaptive_k(tenant_stats, query="hi", config)` — для `doc_count=1000`, `query="hi"` (len=2 < 5) → `K_lexical=50` (30+20), `K_vector=20` (30-10).
- `compute_adaptive_k(tenant_stats, query="very long query with many words here", config)` — для `doc_count=2000000`, `query_len=37 > 20` → `K_lexical=90` (100-10), `K_vector=120` (100+20).
- `POST /search` с `adaptive_k_enabled=True` — K меняется в зависимости от tenant stats. В `debug` добавить `adaptive_k_lexical`, `adaptive_k_vector`.
- `POST /search` с `adaptive_k_enabled=False` — статические K из config (regression test).
- `update_avg_query_len()` — после 100 searches `avg_query_len` обновлён, отражает среднюю длину.
- `update_doc_count()` — cron обновляет `doc_count` для каждого тенанта.
- Redis cache: `doc_count` кэширован (TTL 1 час), search не делает лишний PG round-trip.
- Push-down integration: при `K_lexical=100`, `max_candidate_ids` растёт пропорционально.
- Метрики `adaptive_k_used_total`, `adaptive_k_lexical`, `adaptive_k_vector` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_adaptive_k_test.py` — зелёные.

### Forbidden patterns

- Не вычислять adaptive K на каждый search через PG round-trip — Redis кэш для `doc_count`.
- Не допускать `K_lexical < 10` или `K_vector < 10` — минимумы.
- Не допускать `K > 200` — максимумы (защита от вырождения).
- Не забывать fallback на статический K если `tenant_stats is None` (новый тенант).
- Не обновлять `doc_count` на каждый search — только cron (daily) или on-demand.
- Не применять adaptive K если `feature_flags.adaptive_k_enabled=False`.

### Suggested files

- `alembic/versions/010_tenant_search_stats.py` — миграция (номер может отличаться, проверить последовательность)
- `app/db/models/tenant_search_stat.py` — SQLAlchemy модель
- `app/db/queries/tenant_stats.py` — CRUD + running average
- `app/search/adaptive_k.py` — `compute_adaptive_k` (заменить заготовку из PR-00)
- `app/search/orchestrator.py` — интеграция adaptive K
- `app/db/queries/tenants.py` — расширить `get_tenant_doc_count` для использования `tenant_search_stats`
- `scripts/update_tenant_stats.py` — daily cron
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_adaptive_k_test.py`

---

## PR-11 — Инкрементальные фасеты: Redis-кэш `facets:{query_hash}:{page}`

> **Компонент ROADMAP §5.2.11** | **Артефакт**: `app/search/facets_incremental.py`, Redis-кэш, incremental merge

### Context

Critical-фаза (C-06) реализовала фасеты через полный SQL-запрос на каждый `POST /search` с `facets=["tags", "attributes.category"]`. При бесконечном скролле (load more) — клиент запрашивает следующую страницу, и фасеты пересчитываются с нуля. Для крупных тенантов это лишняя нагрузка. Решение: Redis-кэш `facets:{query_hash}:{page}` (TTL 10 минут) хранит предыдущий результат; при load more инкрементально добавляем новые документы (только delta), а не пересчитываем весь SQL. Это local update вместо global recompute (TRIZ §3).

### Goal

1. Реализовать `app/search/facets_incremental.py::IncrementalFacets`:
   - `async def get_or_compute(self, session, redis_client, doc_ids: list[UUID], facet_fields: list[str], query_hash: str, page: int, top_n: int) -> dict[str, list[FacetBucket]]` — проверяет Redis-кэш `facets:{query_hash}:{page}`. Если hit — возвращает кэшированные facets. Если miss — вычисляет через `compute_facets` (C-06), кэширует, возвращает.
   - `async def merge_incremental(self, cached: dict[str, list[FacetBucket]], new_doc_ids: list[UUID], session, facet_fields: list[str]) -> dict[str, list[FacetBucket]]` — для load more: берёт кэшированные facets, добавляет counts для `new_doc_ids` (delta computation), возвращает merged result. Кэширует обновлённый результат.
2. Интегрировать в `SearchOrchestrator`: если `feature_flags.incremental_facets_enabled` и `req.facets` — использовать `IncrementalFacets.get_or_compute` вместо `compute_facets`. Для load more (запрос с `page` параметром) — `merge_incremental`.
3. Добавить опциональное поле `page: int = 1` в `SearchRequest` — для load more (страница результатов).

### Constraints

- Redis-кэш key: `facets:{sha256(query + str(filters) + str(tenant_id))}:{page}`. TTL: `IncrementalFacetsConfig.cache_ttl_seconds` (600 = 10 минут).
- Кэшируется только facets aggregation, не hits (hits меняются при reindex).
- Cache hit: `redis_client.get(key)` → deserialize (JSON) → return facets. Если hit — 0 SQL queries.
- Cache miss: `compute_facets(doc_ids, ...)` (C-06) → serialize → `redis_client.set(key, json, ex=ttl)`.
- Incremental merge (load more): cached facets + `new_doc_ids` (delta) → `compute_facets(new_doc_ids, ...)` → merge counts (sum per bucket) → sort by count desc → top_n → cache updated.
- `page` в `SearchRequest`: 1 = первая страница (top-K), 2 = load more (next K), и т.д. Facets кэшируются per-page.
- Сериализация: JSON (FacetBucket → `{"value": str, "count": int}`). Не pickle (security).
- Cache invalidation: при soft-delete документа (P-08) — facets cache не инвалидируется (TTL 10 мин — acceptable staleness). При reindex — facets могут быть слегка stale, это OK.
- Метрики: `incremental_facets_cache_hits_total` (Counter), `incremental_facets_cache_misses_total` (Counter), `incremental_facets_merge_total` (Counter — сколько load more запросов использовали incremental merge).
- Если Redis unavailable — fallback на `compute_facets` без кэша (graceful degradation).

### References

- **ARCHITECT §8.5** — Инкрементальные фасеты (Redis-кэш, incremental merge, TTL).
- **ARCHITECT §8.4** — Фасеты в PostgreSQL (базовая `compute_facets` из C-06).
- **ROADMAP §5.2.11** — параметры: Redis-кэш `facets:{query_hash}:{page}`, TTL 10 мин.
- **ROADMAP §10.3 TRIZ §3** — «Местное качество»: local update вместо global recompute.
- **BACKLOG.md §3.1 PR-11** — зависимости (C-06), артефакты.

### Acceptance Criteria

- `IncrementalFacets.get_or_compute()` — первый вызов (cache miss): вычисляет, кэширует, возвращает facets. Второй вызов (cache hit): возвращает из кэша (0 SQL queries).
- `merge_incremental()` — cached facets + new_doc_ids → merged counts. Проверить: bucket counts = cached + delta.
- `POST /search` с `facets=["tags"]`, `page=1` — cache miss, вычисляет, кэширует `facets:{hash}:1`.
- `POST /search` с `facets=["tags"]`, `page=1` (повторно) — cache hit, 0 SQL queries для facets.
- `POST /search` с `facets=["tags"]`, `page=2` (load more) — `merge_incremental`, merged facets.
- Redis unavailable — fallback на `compute_facets` без кэша, search не падает.
- TTL: через 10 минут cache expired, повторный запрос — cache miss.
- Метрики `incremental_facets_cache_hits_total`, `incremental_facets_cache_misses_total` — экспонируются. Hit rate = hits / (hits + misses).
- `feature_flags.incremental_facets_enabled=False` — использует `compute_facets` напрямую (regression test из C-06).
- `ruff check`, `mypy app/`, `pytest tests/unit/test_incremental_facets_test.py` — зелёные.

### Forbidden patterns

- Не кэшировать hits — только facets (hits меняются при reindex).
- Не использовать pickle для сериализации — JSON (security).
- Не инвалидировать facets cache на каждый soft-delete — TTL 10 мин acceptable staleness.
- Не блокировать search если Redis unavailable — fallback на `compute_facets`.
- Не кэшировать facets для разных `facet_fields` под одним key — key включает `facet_fields` (через query_hash).
- Не забывать `page` в key — иначе load more перезаписывает page 1.

### Suggested files

- `app/search/facets_incremental.py` — `IncrementalFacets` (заменить заготовку из PR-00)
- `app/search/orchestrator.py` — интеграция `IncrementalFacets`
- `app/api/schemas.py` — добавить `page: int = Field(1, ge=1)` в `SearchRequest`
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_incremental_facets_test.py`

---

## PR-12 — Многоязычность enhanced: fasttext-langid, смешанные запросы, транслитерация

> **Компонент ROADMAP §5.2.12** | **Артефакт**: `app/search/langid_fasttext.py`, token-level language detection, `cyrtranslit`, NFC-нормализация

### Context

MVP (P-07) использует `langdetect` для определения языка документа и запроса. Это работает для моноязычных запросов, но падает на смешанных: «купить iPhone 15 pro в Москве» — langdetect вернёт один язык (русский или английский), и лексический канал промахнётся на половине токенов. Production-Ready решает это: `fasttext-langid` (быстрее и точнее), смешанные запросы (token-level detection — латиница → latin sub-query, кириллица → cyrillic sub-query, цифры/бренды → neutral), транслитерация (`cyrtranslit` — для лексического канала, чтобы «Айфон» и «iPhone» совпадали), Unicode NFC-нормализация (снимает класс ложных несовпадений — композитные vs декомпозированные символы).

### Goal

1. Реализовать `app/search/langid_fasttext.py::FastTextLangId`:
   - `def detect(self, text: str) -> tuple[str, float]` — возвращает `(language, confidence)`. Использует `fasttext-langid` модель (lid.176.ftz). Fallback на tenant default language при `confidence < 0.7`.
   - `def detect_token_languages(self, query: str) -> dict[str, list[str]]` — token-level detection: группирует токены по скрипту (latin, cyrillic, digits, other). Возвращает `{"latin": [...], "cyrillic": [...], "neutral": [...]}`.
2. Реализовать смешанные запросы для лексического канала (ARCHITECT §9.4):
   - Для векторного канала — исходный запрос целиком (bge-m3 сам разбирает смешанность).
   - Для лексического — два параллельных ts-запроса: один для latin sub-query, один для cyrillic sub-query, объединяемых через `tsquery && tsquery`. Повышает recall на смешанных интентах на 6–8%.
3. Реализовать транслитерацию: `cyrtranslit` — конвертирует кириллицу ↔ латиницу. Для лексического канала: если запрос на кириллице, дополнительно ищем транслитерированную версию (и наоборот).
4. Unicode NFC-нормализация: `unicodedata.normalize("NFC", text)` + нижний регистр — перед всеми операциями поиска/индексации.
5. Интегрировать в `lexical_search` (P-09): заменить `langdetect` на `FastTextLangId`, добавить смешанные ts-запросы, транслитерацию, NFC-нормализацию.

### Constraints

- `fasttext-langid`: модель `lid.176.ftz` (917KB, 176 языков). Загрузка lazy при первом вызове. Thread-safe через `asyncio.to_thread` (inference синхронный).
- Confidence threshold: `fasttext-langid` возвращает confidence. Если `< 0.7` — fallback на tenant default language (или "en").
- Token-level detection: по Unicode скрипту токена:
  - Latin (U+0041–U+005A, U+0061–U+007A, + extended) → latin sub-query.
  - Cyrillic (U+0400–U+04FF) → cyrillic sub-query.
  - Digits (U+0030–U+0039) → neutral (идут в оба канала).
  - Other → neutral.
- Смешанные ts-запросы: `plainto_tsquery('simple', latin_subquery) && plainto_tsquery('simple', cyrillic_subquery)` — объединение через `&&` (AND). Или `||` (OR) — зафиксировать `&&` (AND, более точный).
- Транслитерация: `cyrtranslit.to_latin(text)` (кириллица → латиница), `cyrtranslit.to_cyrillic(text)` (латиница → кириллица). Для лексического канала: оригинальный запрос + транслитерированный — оба в ts-запросе (через `||` OR).
- NFC-нормализация: `unicodedata.normalize("NFC", text)` — применяется в `IndexingService.create()` (P-07) и в `SearchOrchestrator` перед lexical/vector search.
- Для векторного канала — исходный запрос целиком, без token-level split (bge-m3 multilingual).
- Performance: `fasttext-langid` inference ~0.1ms (быстрее `langdetect` ~1ms). Token-level split — O(n) где n = tokens.
- Метрики: `langid_fasttext_used_total` (Counter, label `language`), `mixed_queries_detected_total` (Counter), `transliteration_used_total` (Counter).
- `feature_flags.mixed_language_enabled` — если False, используется моноязычный path (как MVP P-07).

### References

- **ARCHITECT §9.4** — Смешанные запросы (token-level detection, два ts-запроса, `&&`).
- **ARCHITECT §9.3** — Транслитерация и нормализация (`cyrtranslit`, NFC).
- **ARCHITECT §9.1** — Языковая детекция (fasttext-langid, confidence, fallback).
- **ARCHITECT §9.2** — Cross-lingual search (векторный канал, bge-m3).
- **ROADMAP §5.2.12** — параметры: fasttext-langid, смешанные запросы, транслитерация, NFC.
- **ROADMAP §10.3 TRIZ §1** — «Сегментация»: смешанные запросы token-level.
- **ROADMAP §10.3 TRIZ §35** — «Изменение параметров»: NFC-нормализация.
- **BACKLOG.md §3.1 PR-12** — зависимости (P-09), артефакты.

### Acceptance Criteria

- `FastTextLangId.detect("купить iPhone")` — возвращает `("ru", 0.85)` или `("en", 0.6)` (зависит от доминирующего скрипта). Confidence > 0.7 для чистых языков.
- `FastTextLangId.detect_token_languages("купить iPhone 15 pro")` — `{"latin": ["iPhone", "pro"], "cyrillic": ["купить"], "neutral": ["15"]}`.
- `POST /search` с `query="купить iPhone 15 pro"` — lexical channel использует два ts-запроса (latin + cyrillic), recall выше чем моноязычный (проверить через eval: recall@10 улучшается на смешанных запросах).
- Транслитерация: запрос `"Айфон"` → также ищет `"Aifon"` (transliterated). И наоборот.
- NFC-нормализация: документ с композитными символами (é = U+0065 + U+0301) — нормализуется, совпадает с запросом с декомпозированным.
- `fasttext-langid` confidence < 0.7 → fallback на tenant default language.
- `feature_flags.mixed_language_enabled=False` — моноязычный path (как P-07, regression test).
- Performance: langid inference < 1ms, lexical search latency не деградировала.
- Метрики `langid_fasttext_used_total`, `mixed_queries_detected_total`, `transliteration_used_total` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_fasttext_langid_test.py` — зелёные.

### Forbidden patterns

- Не использовать `langdetect` для новых запросов — только `fasttext-langid` (быстрее, точнее). `langdetect` можно оставить как fallback.
- Не split векторный канал — bge-m3 multilingual, исходный запрос целиком.
- Не использовать `tsquery ||` для смешанных (OR) — `&&` (AND) точнее (зафиксировать).
- Не забыть NFC-нормализацию в `IndexingService` (P-07) — иначе документы с композитными символами не найдутся.
- Не блокировать search path на `fasttext-langid` inference — `asyncio.to_thread` (inference синхронный).
- Не хардкодить threshold 0.7 — через config (`fasttext_confidence_threshold`).

### Suggested files

- `app/search/langid_fasttext.py` — `FastTextLangId` (заменить заготовку из PR-00)
- `app/search/lexical.py` — интеграция смешанных ts-запросов, транслитерации, NFC
- `app/services/indexing.py` — NFC-нормализация при индексации
- `app/embedding/service.py` — NFC перед embed_query
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_fasttext_langid_test.py`
- `tests/unit/test_mixed_queries_test.py` (если отдельный)

---

## PR-13 — Асимметричное индексирование: chunking + взвешенное среднее секций

> **Компонент ROADMAP §5.2.13** | **Артефакт**: `app/embedding/chunking.py`, chunking 512 overlap 50, взвешенное среднее (title × 3, abstract × 2, body × 1), агрегация `max(score)` по `doc_id`

### Context

MVP (P-05) индексирует один embedding на весь документ. Для коротких документов это работает, но длинные (> 512 токенов) теряют детали: один embedding усредняет всё, и поиск по конкретному фрагменту промахивается. Структурированные документы (с известными секциями title, abstract, body) — ещё хуже: title важнее body, но усреднение нивелирует это. Production-Ready решает через асимметричное индексирование: короткие документы — один embedding; длинные — chunking по 512 токенов с overlap 50, несколько точек в Qdrant с общим `doc_id` и `chunk_idx`. Структурированные — взвешенное среднее embeddings секций (title × 3, abstract × 2, body × 1). На этапе поиска — агрегация через `max(score)` по `doc_id`.

### Goal

1. Реализовать `app/embedding/chunking.py::ChunkingService`:
   - `def chunk_document(self, title: str, content: str, attributes: dict) -> list[DocumentChunk]` — разбивает документ на chunks:
     - Если `len(tokenize(content)) < 200` → один chunk (whole document).
     - Если `200 <= len < 512` → один chunk с взвешенным средним (title × 3, body × 1).
     - Если `len >= 512` → chunking по 512 токенов с overlap 50. Каждый chunk: `DocumentChunk(text, chunk_idx, section_type)`.
   - `def weighted_average(self, section_embeddings: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray` — взвешенное среднее: `title × 3, abstract × 2, body × 1`. Нормализованный результат.
2. Расширить `QdrantPayload` (P-04): добавить поля `chunk_idx: int = 0`, `section_type: str | None = None`. Обновить schema drift контракт-тесты (PR-14).
3. Интегрировать в `EmbeddingService.embed_texts` (или отдельный метод `embed_document`): для каждого chunk вычислить embedding, upsert в Qdrant с `doc_id` (общий) + `chunk_idx` (уникальный per chunk).
4. Интегрировать в `vector_search` (P-10): Qdrant kNN возвращает несколько points с одним `doc_id` (разные chunks). Агрегация: `max(score)` по `doc_id` — документ ранжируется по лучшему chunk.
5. Интегрировать в `IndexingService` (P-07): при `feature_flags.asymmetric_indexing_enabled` — использовать `ChunkingService` вместо `embed_texts([content])`.

### Constraints

- Chunking: 512 токенов с overlap 50. Tokenizer: тот же, что у bge-m3 (или `tiktoken` для подсчёта). Overlap предотвращает потерю контекста на границах chunks.
- Взвешенное среднее секций:
  - `title` — вес 3 (самый важный, короткий, точный).
  - `abstract` — вес 2 (если есть, в `attributes.abstract`).
  - `body` — вес 1 (content).
  - `weighted_avg = (3 * emb_title + 2 * emb_abstract + 1 * emb_body) / (3 + 2 + 1)`. Нормализованный.
  - Для коротких документов без sections — один embedding на `title + "\n" + content`.
- Qdrant: несколько points с одним `doc_id`, разными `chunk_idx`. Payload включает `chunk_idx`, `section_type`.
- Vector search aggregation: `max(score)` по `doc_id`. Если 3 chunks одного документа в top-K — берём max, документ ранжируется по лучшему chunk. Дедупликация по `doc_id` в `vector_search` (P-10).
- Удаление (soft_delete, P-08): удаляются все chunks (все points с `doc_id`). Qdrant `delete` по `filter` `doc_id == ...`.
- Reconciler (P-13): upsert/delete обрабатывает все chunks. `content_hash` fast-path — если контент не изменился, все chunks пропускаются.
- Performance: chunking добавляет latency на indexing (больше embeddings). Для 1 chunk — как раньше. Для 5 chunks — 5× embedding compute (но кэш content-hash сработает для повторяющихся секций).
- Метрики: `asymmetric_indexing_chunks_total` (Counter, label `section_type`), `asymmetric_indexing_docs_total` (Counter, label `chunking_type` — "single"/"multi"/"weighted"), `vector_search_chunk_aggregation_total` (Counter — сколько раз применили max(score)).
- `feature_flags.asymmetric_indexing_enabled=False` — один embedding на документ (как MVP P-05, regression).

### References

- **ARCHITECT §5.6** — Асимметричное индексирование (chunking, overlap, взвешенное среднее).
- **ARCHITECT §5.1** — Выбор модели (bge-m3, max_length 512).
- **ARCHITECT §5.2** — Батчинг и параллелизм (chunking батчи).
- **ARCHITECT §3.2** — Qdrant payload (`QdrantPayload` — расширять `chunk_idx`, `section_type`).
- **ROADMAP §5.2.13** — параметры: chunking 512 overlap 50, взвешенное среднее, max(score).
- **ROADMAP §10.3 TRIZ §3** — «Местное качество»: разная стратегия под разный контент.
- **BACKLOG.md §3.1 PR-13** — зависимости (P-05), артефакты.

### Acceptance Criteria

- `ChunkingService.chunk_document(title="Test", content="short content")` — 1 chunk (short document).
- `ChunkingService.chunk_document(title="Test", content=<2000 tokens>)` — несколько chunks (512 + overlap 50). Проверить: chunks overlap на 50 токенов.
- Взвешенное среднее: `weighted_average({"title": emb1, "body": emb2}, {"title": 3, "body": 1})` — `0.75 * emb1 + 0.25 * emb2` (нормализованный).
- `POST /index` с длинным документом (> 512 токенов) — создаёт несколько Qdrant points с одним `doc_id`, разными `chunk_idx`. Проверить: `qdrant_client.scroll(filter=doc_id==...)` → несколько points.
- `POST /search` — vector channel возвращает документы по `max(score)` по chunks. Документ с 3 chunks ранжируется по лучшему chunk.
- `DELETE /index/{doc_id}` — удаляет все chunks (все points с `doc_id`).
- `feature_flags.asymmetric_indexing_enabled=False` — один embedding на документ (regression test из P-05).
- Взвешенное среднее: документ с `title`, `attributes.abstract`, `content` — embedding = взвешенное среднее (title × 3, abstract × 2, body × 1).
- Метрики `asymmetric_indexing_chunks_total`, `vector_search_chunk_aggregation_total` — экспонируются.
- `ruff check`, `mypy app/`, `pytest tests/unit/test_chunking_test.py` — зелёные.

### Forbidden patterns

- Не использовать один embedding для длинных документов — chunking обязателен при `len >= 512`.
- Не забывать overlap 50 — без него теряется контекст на границах chunks.
- Не хранить chunks без `chunk_idx` — иначе дедупликация по `doc_id` невозможна.
- Не забыть `max(score)` агрегацию в vector search — иначе документ дублируется в top-K.
- Не забыть обновить `QdrantPayload` (PR-14) — `chunk_idx`, `section_type` обязательны.
- Не забыть soft_delete обрабатывает все chunks — `delete` по filter, не по ID.
- Не использовать chunking без feature flag — regression path должен работать (один embedding).

### Suggested files

- `app/embedding/chunking.py` — `ChunkingService` (заменить заготовку из PR-00)
- `app/search/qdrant_payload.py` — расширить `QdrantPayload` (`chunk_idx`, `section_type`)
- `app/embedding/service.py` — `embed_document` метод
- `app/search/vector.py` — `max(score)` агрегация по `doc_id`
- `app/services/indexing.py` — интеграция `ChunkingService`
- `app/services/qdrant.py` — delete по filter (все chunks)
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_chunking_test.py`

---

## PR-14 — Schema drift контракт-тесты: Qdrant payload ↔ Postgres documents

> **Компонент ROADMAP §5.2.14** | **Артефакт**: `tests/contract/test_schema_drift.py`, CI gate, единый `QdrantPayload`

### Context

Dual-write (Postgres + Qdrant) создаёт риск schema drift: если в Postgres добавили колонку, но забыли обновить Qdrant payload (или наоборот) — данные расходятся, и векторный канал начинает возвращать неполные/некорректные результаты. MVP (P-04) создал единый `QdrantPayload` pydantic-модели с `extra="forbid"`, но это не проверяется автоматически. Production-Ready добавляет контракт-тесты в CI: payload точки Qdrant соответствует схеме `documents` Postgres, и наоборот. Это изолированный компонент (ROADMAP §8.3) — добавляется в CI в любой момент.

### Goal

1. Реализовать `tests/contract/test_schema_drift.py`:
   - `test_qdrant_payload_matches_documents_schema()` — для каждого поля `QdrantPayload` проверяет, что соответствующее поле существует в `documents` Postgres (через SQLAlchemy model inspection). Например, `QdrantPayload.doc_id` ↔ `Document.id`, `QdrantPayload.tenant_id` ↔ `Document.tenant_id`, `QdrantPayload.model_name` ↔ `Document.embedding_model`.
   - `test_documents_fields_covered_in_payload()` — для каждого поля `documents` Postgres, которое должно быть в payload (определяется allowlist), проверяет, что оно есть в `QdrantPayload`.
   - `test_qdrant_payload_extra_forbid()` — `QdrantPayload` имеет `model_config = ConfigDict(extra="forbid")`. Попытка создать `QdrantPayload(unknown_field="x")` → `ValidationError`.
   - `test_qdrant_point_payload_roundtrip()` — вставить документ через `IndexingService`, прочитать Qdrant point payload, десериализовать в `QdrantPayload` — должно совпасть.
   - `test_chunk_fields_in_payload()` (после PR-13) — `QdrantPayload` содержит `chunk_idx`, `section_type`.
2. Добавить CI gate: в `scripts/run_dod_check_prod.sh` (или `run_dod_check.sh`) — `pytest tests/contract/` должен быть зелёным.
3. Документировать процесс изменения schema: при изменении `documents` (миграция) или `QdrantPayload` — обновить контракт-тесты синхронно.

### Constraints

- Единый `QdrantPayload` (P-04) — source of truth. Все изменения payload — через расширение модели, не дубликаты.
- `extra="forbid"` на `QdrantPayload` — предотвращает silent schema drift (неизвестные поля → ValidationError).
- Allowlist полей `documents`, которые должны быть в payload: `id` (→ `doc_id`), `tenant_id`, `language`, `tags`, `attributes`, `embedding_model` (→ `model_name`), `embedding_rev` (→ `model_rev`), `content_hash`, `created_at`. Не в payload: `title`, `content` (ARCHITECT §3.2 — не хранятся в Qdrant, batch-loaded из Postgres), `tsv` (generated), `updated_at`, `deleted_at`.
- После PR-13: `chunk_idx`, `section_type` — добавляются в payload. Контракт-тест проверяет их наличие.
- Roundtrip test: `POST /index` → Qdrant point → `QdrantPayload(**payload)` — должно совпасть с исходными данными.
- CI gate: `pytest tests/contract/test_schema_drift.py` — обязателен для release. Fails → release blocked.
- Миграции: при `alembic upgrade` (изменение `documents` schema) — соответствующее обновление `QdrantPayload` и контракт-тестов.
- Метрики: нет (контракт-тесты — это CI, не рантайм). Но можно добавить `schema_drift_tests_passed` Gauge (0 или 1) — обновляется CI.

### References

- **ARCHITECT §15.4** — Schema drift (единый source of truth, CI gate).
- **ARCHITECT §3.2** — Qdrant payload (`QdrantPayload` модель, `extra="forbid"`).
- **ARCHITECT §3.1** — PostgreSQL `documents` схема.
- **ROADMAP §5.2.14** — параметры: CI gate, единый `QdrantPayload`.
- **BACKLOG.md §3.1 PR-14** — зависимости (P-04), артефакты.
- **ROADMAP §8.3** — изолированный компонент, добавляется в CI в любой момент.

### Acceptance Criteria

- `test_qdrant_payload_matches_documents_schema()` — для каждого поля `QdrantPayload` проверяет соответствие в `documents`. Pass.
- `test_documents_fields_covered_in_payload()` — для каждого allowlist поля `documents` — проверяет наличие в `QdrantPayload`. Pass.
- `test_qdrant_payload_extra_forbid()` — `QdrantPayload(unknown_field="x")` → `ValidationError`. Pass.
- `test_qdrant_point_payload_roundtrip()` — вставить документ, прочитать payload, десериализовать — совпадает. Pass.
- `test_chunk_fields_in_payload()` (после PR-13) — `chunk_idx`, `section_type` в `QdrantPayload`. Pass.
- CI gate: `pytest tests/contract/` — зелёный для release.
- При изменении `QdrantPayload` (добавление поля) без обновления `documents` — `test_qdrant_payload_matches_documents_schema` fails.
- При изменении `documents` (миграция) без обновления `QdrantPayload` — `test_documents_fields_covered_in_payload` fails.
- `ruff check`, `mypy app/`, `pytest tests/contract/test_schema_drift.py` — зелёные.

### Forbidden patterns

- Не дублировать схему в Qdrant payload — единый `QdrantPayload` (P-04).
- Не отключать `extra="forbid"` — это защита от silent drift.
- Не добавлять `title`, `content` в Qdrant payload — они batch-loaded из Postgres (ARCHITECT §3.2).
- Не забывать обновлять контракт-тесты при изменении schema — CI gate блокирует release.
- Не хранить PII в Qdrant payload — только структурные поля (tenant_id, language, tags, attributes, model_name).

### Suggested files

- `tests/contract/__init__.py`
- `tests/contract/test_schema_drift.py`
- `scripts/run_dod_check_prod.sh` — добавить `pytest tests/contract/`
- `app/search/qdrant_payload.py` — документировать allowlist
- `docs/schema_drift.md` — процесс изменения schema (опционально)

---

## PR-15 — Negative cache для embeddings: Redis `null` cache TTL 60 сек

> **Компонент ROADMAP §5.2.15** | **Артефакт**: `app/embedding/cache.py` extension, Redis `null` cache для сломанных входов

### Context

Embedding cache (P-06) хранит только успешные embeddings. Если модель возвращает пустой/невалидный результат (например, пустая строка, бинарный мусор, слишком длинный текст) — кэш miss, и каждый повторный запрос с тем же сломанным входом снова вызывает модель. Это cache miss avalanche: 1000 запросов со сломанным входом → 1000 вызовов модели. Negative cache решает: для сломанных входов кэшируем `null` на 60 секунд. Повторные запросы с тем же входом → cache hit (null) → нет вызова модели. Это изолированный компонент (ROADMAP §8.3) — добавляется в `EmbeddingCache` в любой момент.

### Goal

1. Расширить `app/embedding/cache.py::EmbeddingCache`:
   - `async def get_negative_cache(self, text_hash: str, model_name: str) -> bool` — проверяет Redis key `neg:{model_name}:{text_hash}`. Возвращает `True` если кэширован `null` (сломанный вход).
   - `async def set_negative_cache(self, text_hash: str, model_name: str) -> None` — устанавливает `neg:{model_name}:{text_hash}` = `"null"` с TTL `NegativeCacheConfig.null_ttl_seconds` (60).
   - `async def clear_negative_cache(self, text_hash: str, model_name: str) -> None` — удаляет negative cache entry (если вход исправлен).
2. Интегрировать в `compute_with_cache` (P-06): перед вызовом `compute_fn` — проверить negative cache. Если hit → пропустить (возвращать пустой результат). После `compute_fn` — если результат пустой/невалидный → `set_negative_cache`.
3. Интегрировать в `embed_query` (P-05): та же логика — negative cache для query embeddings.
4. Определить "сломанный вход" (invalid result):
   - `compute_fn` возвращает empty array (`[]`).
   - `compute_fn` возвращает array with NaN/Inf.
   - `compute_fn` raises exception (model error) — кэшировать exception type, не повторять.
   - Text слишком длинный (> `max_length * 2`) — кэшировать null.

### Constraints

- Redis key: `neg:{model_name}:{sha256(text)}`. TTL: `NegativeCacheConfig.null_ttl_seconds` (60 секунд — короткий, чтобы не кэшировать сломанный вход долго).
- Value: `"null"` (string sentinel) или `"1"` — главное, что key существует.
- `get_negative_cache` возвращает `True` если key exists → пропустить compute.
- После `compute_fn`: если result empty/NaN/exception → `set_negative_cache`. Если result valid → не трогать negative cache (мог быть старый false negative — `clear_negative_cache` опционально).
- TTL 60 секунд — компромисс: достаточно короткий, чтобы при исправлении входа (например, модель обновилась) кэш быстро инвалидируется; достаточно длинный, чтобы защитить от avalanche в пиковый час.
- Staggered TTL: для избежания thundering herd (все negative cache entries истекают одновременно) — добавить jitter: `ttl = null_ttl_seconds + random.randint(-10, 10)`.
- `feature_flags.negative_cache_enabled=False` — negative cache отключён, `compute_with_cache` работает как P-06 (без negative cache).
- Метрики: `negative_cache_hits_total` (Counter — сколько раз избежали вызова модели), `negative_cache_sets_total` (Counter — сколько сломанных входов закэшировано), `negative_cache_hit_rate` (Gauge — hits / (hits + misses), для мониторинга).
- Не кэшировать `null` для валидных результатов — только для сломанных/пустых.
- `clear_negative_cache` — опционально, вызывается при reindex (если документ обновился, старый negative cache может быть false negative).

### References

- **ARCHITECT §5.3** — Кэш (content-hash cache, query cache — negative cache extension).
- **ARCHITECT §5.1** — Выбор модели (bge-m3, max_length — сломанные входы).
- **ROADMAP §5.2.15** — параметры: Redis `null` cache TTL 60 сек.
- **ROADMAP §10.3 TRIZ §16** — «Частичное/избыточное действие»: negative cache предотвращает долбление модели.
- **BACKLOG.md §3.1 PR-15** — зависимости (P-06), артефакты.
- **ROADMAP §8.3** — изолированный компонент, добавляется в `EmbeddingCache` в любой момент.

### Acceptance Criteria

- [x] `EmbeddingCache.get_negative_cache(text_hash, model_name)` — `True` если key exists, `False` если miss.
- [x] `EmbeddingCache.set_negative_cache(text_hash, model_name)` — устанавливает key с TTL 60 сек.
- [x] `compute_with_cache` — перед `compute_fn` проверяет negative cache. Если hit → пропускает compute, возвращает empty result.
- [x] `compute_with_cache` — после `compute_fn` с empty/NaN result → `set_negative_cache`.
- [x] 1000 запросов со сломанным входом → 1 вызов модели (первый), 999 negative cache hits. `negative_cache_hits_total = 999`.
- [x] Staggered TTL: TTL варьируется ±10 секунд (проверить через `redis_client.ttl(key)` — в диапазоне 50-70).
- [x] `feature_flags.negative_cache_enabled=False` — negative cache отключён, каждый запрос вызывает модель (regression test из P-06).
- [x] `clear_negative_cache(text_hash, model_name)` — удаляет key.
- [x] Метрики `negative_cache_hits_total`, `negative_cache_sets_total`, `negative_cache_hit_rate` — экспонируются.
- [x] `ruff check`, `mypy app/`, `pytest tests/unit/test_negative_cache_test.py` — зелёные.

### Forbidden patterns

- Не кэшировать `null` для валидных результатов — только для сломанных/пустых.
- Не использовать TTL > 120 секунд — иначе при исправлении входа кэш слишком долго возвращает null.
- Не забывать staggered TTL (jitter) — иначе thundering herd.
- Не кэшировать exception details — только sentinel `"null"` (не логировать exception в Redis).
- Не применять negative cache если `feature_flags.negative_cache_enabled=False`.

### Suggested files

- `app/embedding/cache.py` — расширить `EmbeddingCache` (negative cache methods)
- `app/observability/metrics.py` — новые метрики
- `tests/unit/test_negative_cache_test.py`

---

## Приложение A. Чек-лист готовности Production-Ready к сдаче

Перед запуском финальной проверки (PR-15 DoD) убедиться, что выполнены:

- [ ] **PR-00** — конфиг расширен 9 новыми секциями, зависимости установлены, 10 заготовок модулей созданы, docker-compose расширен otel-collector/tempo/loki/minio.
- [ ] **PR-01** — `users` таблица с `pgvector`, `PersonalizationService` (EMA, adaptive α), `POST /search?personalize=true`, `DELETE /users/{user_id}` (GDPR), `GET /users/{user_id}/profile`.
- [ ] **PR-02** — `search_events` daily-партиционирование, S3 Parquet архивация 90 дней, `DROP PARTITION`, `POST /search/click`, cron `run_partition_management.py`.
- [ ] **PR-03** — A/B-фреймворк: sticky distribution, sequential testing, `experiment_id` в API, daily metrics, `POST /experiments`, `GET /experiments/{id}/metrics`.
- [ ] **PR-04** — MMR diversify: `mmr_diversify` (λ=0.7, adaptive), `diversify=true` в API, diversity_score метрика.
- [ ] **PR-05** — Контекстная персонализация: `SearchContext {device, session_id, time_of_day}`, multiple profile vectors, выбор по контексту, session profile TTL 24h.
- [ ] **PR-06** — Distributed tracing: OTel SDK, spans (`search.request` + дочерние), export в Tempo, `trace_id` correlation с Loki.
- [ ] **PR-07** — Миграционный фреймворк: blue-green, backfill, canary 1%, snapshot-on-reindex, rollback, CLI `python -m app.migration`, dual-write.
- [ ] **PR-08** — Shadow traffic: параллельный расчёт, `variant` колонка в `search_events`, sampling, control/treatment логирование.
- [ ] **PR-09** — Chaos engineering: 3 сценария (kill Qdrant, delay reranker, fill Redis), weekly cron, postcondition-чеки в CI, production guard.
- [ ] **PR-10** — Adaptive K: `tenant_search_stats` таблица, `compute_adaptive_k`, адаптивные K_lexical/K_vector в рантайме.
- [ ] **PR-11** — Инкрементальные фасеты: Redis-кэш `facets:{query_hash}:{page}`, TTL 10 мин, incremental merge для load more.
- [ ] **PR-12** — Многоязычность enhanced: `fasttext-langid`, смешанные запросы token-level, `cyrtranslit`, NFC-нормализация.
- [x] **PR-13** — Асимметричное индексирование: chunking 512 overlap 50, взвешенное среднее (title × 3, abstract × 2, body × 1), `max(score)` агрегация.
- [x] **PR-14** — Schema drift контракт-тесты: `tests/contract/test_schema_drift.py`, CI gate, `QdrantPayload extra="forbid"`.
- [x] **PR-15** — Negative cache: Redis `null` cache TTL 60 сек, staggered TTL, `negative_cache_hits_total` метрика.
- [ ] **TRIZ-gates Production-Ready** (ROADMAP §5.3 / §10.3) — все 15 встроены:
  - [ ] Adaptive `α` в EMA (PR-01)
  - [ ] MMR anti-bubble (PR-04)
  - [ ] Контекстная персонализация (PR-05)
  - [ ] Shadow traffic (PR-08)
  - [ ] Chaos engineering weekly (PR-09)
  - [ ] Канареечный reindex (PR-07)
  - [ ] Snapshot-on-reindex (PR-07)
  - [ ] Blue-green коллекции (PR-07)
  - [ ] Distributed tracing (PR-06)
  - [ ] Adaptive K (PR-10)
  - [ ] Инкрементальные фасеты (PR-11)
  - [ ] Смешанные запросы (PR-12)
  - [ ] Асимметричное индексирование (PR-13)
  - [x] Schema drift контракт-тесты (PR-14)
  - [ ] Negative cache (PR-15)
- [ ] **DoD Production-Ready** (ROADMAP §5.4) — все условия выполнены:
  - [ ] `profile_vector` обновляется при клике; cold-start → стабилизация через 10 кликов.
  - [ ] A/B-фреймворк: sticky distribution, daily metrics.
  - [ ] OTel traces в Tempo; `trace_id` из Loki открывает trace.
  - [ ] `search_events` партиционируется; архивация 90 дней в S3 Parquet; `DROP PARTITION`.
  - [ ] Смена embedding-модели — zero-downtime: canary 1%, full reindex за часы, rollback из snapshot за минуты.
  - [ ] Shadow traffic: оба варианта в `search_events` с `variant`.
  - [ ] Chaos-сценарии проходят в CI, postcondition-чеки зелёные.
  - [ ] Adaptive K меняется в рантайме по `tenant_search_stats`.
  - [ ] Смешанные запросы возвращают релевантные результаты из обоих языков.
  - [ ] Длинные документы чанкованы, возвращаются по максимальному скору чанка.
- [ ] **NFR Production-Ready** (ROADMAP §5.5) — достигнуты:
  - [ ] Latency p99 `/search` (без rerank) ≤ 120 мс.
  - [ ] Latency p99 `/search` (с rerank) ≤ 350 мс.
  - [ ] Throughput индексации ≥ 2000 doc/min.
  - [ ] Throughput поиска ≥ 500 RPS.
  - [ ] SLA 99.9%.
  - [ ] Lag ≤ 5 секунд.
  - [ ] `embedding_cache_hit_rate` ≥ 60%.
  - [ ] Recall@10 не падает > 2%.
  - [ ] nDCG@10 не падает > 1%.
- [ ] **Exit criteria §9.3** (ROADMAP) — все условия выполнены:
  - [ ] DoD Production-Ready достигнут (§5.4).
  - [ ] NFR Production-Ready достигнуты.
  - [ ] ≥ 4 недели кликовых данных в `search_events` (для Improvements LambdaMART).
  - [ ] OTel end-to-end instrumentation.
  - [ ] Chaos weekly green.
  - [ ] ≥ 1 zero-downtime миграция embedding-модели (через PR-07 framework).
  - [ ] Trigger для Improvements активирован (или осознанно отложен).
- [ ] **Regression checks** — Critical и MVP не сломаны:
  - [ ] `scripts/run_dod_check_critical.sh` — зелёный.
  - [ ] `scripts/run_dod_check.sh` — зелёный.
  - [ ] `tests/e2e/test_critical_dod.py` — зелёный.
  - [ ] `tests/e2e/test_dod_validation.py` — зелёный.
  - [ ] `tests/e2e/test_mvp_dod.py` — зелёный.
- [ ] **Production-Ready DoD** — `scripts/run_dod_check_prod.sh` (создать) — зелёный:
  - [ ] `pytest tests/unit/` — все зелёные.
  - [ ] `pytest tests/integration/` — все зелёные.
  - [x] `pytest tests/contract/` — зелёный (PR-14).
  - [ ] `pytest -m chaos` — зелёный (PR-09, в non-prod).
  - [ ] `pytest tests/e2e/test_prod_dod.py` — зелёный.

## Приложение B. Что НЕ делать в Production-Ready (scope guard)

Список фич, которые **запрещено** реализовывать в этой фазе (они появятся в Improvements, ROADMAP §6):

- ❌ Learned ranking LambdaMART (Improvements, ARCHITECT §7.4, ROADMAP §6.2.1) — требует ≥ 4 недели кликовых данных из `search_events` (PR-02).
- ❌ Каскадный выбор стратегии fusion (Improvements, ARCHITECT §7.5, ROADMAP §6.2.2) — нужен stable cross-encoder + метрики confidence.
- ❌ Multi-armed bandit для `α` (Improvements, ROADMAP §6.2.13) — требует ≥ 1 месяц A/B-данных (PR-03).
- ❌ Тенантное tiering A/B/C (Improvements, ARCHITECT §12.6, ROADMAP §6.2.3) — требует `tenant_search_stats` (PR-10) + enterprise-тенанты.
- ❌ Cost-of-durability per tier (Improvements, ARCHITECT §15.6, ROADMAP §6.2.4) — требует IM-03 (tiering).
- ❌ PostgreSQL read replicas + PgBouncer (Improvements, ROADMAP §6.2.5) — триггер `> 1M документов` или `> 500 RPS`.
- ❌ Qdrant sharding 4 → 8 (Improvements, ROADMAP §6.2.6) — триггер нагрузки.
- ❌ Embedding worker autoscaling (Improvements, ROADMAP §6.2.7) — триггер нагрузки.
- ❌ Redis Cluster (Improvements, ROADMAP §6.2.9) — триггер `> 1M документов`.
- ❌ Connection-aware routing (Improvements, ARCHITECT §12.7, ROADMAP §6.2.10) — требует read replicas.
- ❌ CDC migration Debezium (Improvements, ARCHITECT §15.5, ROADMAP §6.2.11) — триггер `> 100M документов` или `> 10k RPS записи`; нужен migration framework (PR-07) как prerequisite.
- ❌ Адаптивный `k` в RRF (Improvements, ROADMAP §6.2.12) — мягко зависит от PR-10 (`tenant_search_stats`), но `k = 30 + 10 * log(doc_count)` — это Improvements-уровень оптимизации.
- ❌ Онлайн-обучение LambdaMART (Improvements extension) — оффлайн weekly достаточно.
- ❌ Real-time interleaved ranking (Improvements) — shadow traffic (PR-08) достаточно для валидации.
- ❌ Federated search (out of scope) — не в ROADMAP.
- ❌ Cross-tenant personalization (privacy violation) — строго запрещено.

Если в процессе работы над Production-Ready-промптом агент считает, что какая-то из этих фич «нужна прямо сейчас» — это красный флаг. Зафиксировать в `worklog.md` как «отложено до фазы Improvements» и продолжить Production-Ready. Если триггер для Improvements-компонента ещё не достигнут — внедрение преждевременно и добавляет лишнюю сложность.

---

## Приложение C. Зависимости между PR-промптами

```
PR-00 (bootstrap)
├── PR-01 (personalization) ─────────────►  PR-04 (MMR, мягкая зависимость)
│                                            PR-05 (context personalization)
├── PR-02 (search_events) ────────────────►  PR-03 (A/B framework)
│                                            PR-08 (shadow traffic, через PR-03)
│                                            PR-10 (adaptive K)
├── PR-06 (OTel tracing) ─────────────────►  PR-09 (chaos, нужен OTel для postcondition-чеков)
├── PR-07 (migration framework) ─────────►  (зависит от PR-02 + C-07)
├── PR-11 (incremental facets) ──────────►  (зависит от C-06)
├── PR-12 (multilang enhanced) ──────────►  (изолирован, зависит только от P-09)
├── PR-13 (asymmetric indexing) ─────────►  (зависит от P-05; расширяет QdrantPayload → PR-14)
├── PR-14 (schema drift tests) ──────────►  (изолирован, зависит от P-04; проверяет PR-13 расширения)
└── PR-15 (negative cache) ──────────────►  (изолирован, зависит от P-06)
```

**Критический путь** (максимальная длина зависимостей):
`PR-00 → PR-01 → PR-02 → PR-03 → PR-08` (shadow traffic — самый «глубокий» компонент).

**Изолированные компоненты** (можно внедрять в любой момент, ROADMAP §8.3):
- PR-04 (MMR) — может работать на pure fusion (без персонализации).
- PR-12 (Многоязычность enhanced) — нужен только P-09.
- PR-14 (Schema drift контракт-тесты) — добавляется в CI в любой момент.
- PR-15 (Negative cache) — добавляется в `EmbeddingCache` в любой момент.

**Параллелизация** (рекомендуемые группы для одновременной разработки):
- Группа A: PR-01, PR-15, PR-14 (три изолированных компонента).
- Группа B: PR-06, PR-12 (OTel + multilang — разные подсистемы).
- Группа C: PR-10, PR-11, PR-13 (adaptive K, incremental facets, asymmetric indexing — search subsystem extensions).

---

*Документ сопровождает `ARCHITECT.md` v2.0, `ROADMAP.md` v1.0, `BACKLOG.md` v1.0, `MVP-PROMPTS.md` v1.0, `CRITICAL-PROMPTS.md` v1.0 и `BUGFIXES-PROMPTS.md` v1.0. При изменении архитектуры — обновить промпты синхронно; при изменении NFR — обновить Приложение A; при добавлении новых Improvements-триггеров — обновить Приложение B.*

