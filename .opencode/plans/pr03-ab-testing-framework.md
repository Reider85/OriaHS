# PR-03 — A/B-фреймворк: sticky distribution + sequential testing

Источник: `analitics/PROD-PROMPTS.md` §PR-03 (строки 576–661).
Зависимости выполнены: PR-00 (bootstrap, `ABTestingConfig` в `app/config.py:189`), PR-01 (`006_users`), PR-02 (`007_search_events_partitions`, `search_event_queries`). `experiment_id` уже есть в `SearchRequest`/`ClickRequest`, `variant` — в `SearchResponse`.

## Текущее состояние (найдено при ревью)

- `app/services/ab_testing.py` — **заготовка с багом**: `from app.services.ab_testing import ABTestingService` (самоимпорт, ImportError при импорте). Заменяется полностью.
- `search_events.variant` **нет** ни в модели `SearchEvent`, ни в миграциях (по решению пользователя — добавляем в 008).
- Миграция `008` свободна (следующая после `007_search_events_partitions`).
- Нумерация: `008_ab_experiments` по Goal PR-03; §0.4 дерево отдаёт 008 PR-10 (`tenant_search_stats`) — PR-10 позже получит 009. Девиация фиксируется в отчёте.

## Решения (согласованы)

1. Миграция `008_ab_experiments` создаёт таблицу `ab_experiments` **и** добавляет `search_events.variant TEXT` (ALTER TABLE parent — автоматически наследуется всеми daily-партициями + default).
2. Grafana `prod-ab-testing.json` — минимальный (3 панели).

## Шаги реализации

### 1. Миграция + модели

- `alembic/versions/008_ab_experiments.py` (`down_revision="007_search_events_partitions"`):
  - `ab_experiments`: `id SERIAL PK`, `experiment_id TEXT UNIQUE NOT NULL`, `description TEXT`, `variants JSONB NOT NULL`, `weights JSONB`, `status TEXT NOT NULL DEFAULT 'active'`, `start_date DATE`, `end_date DATE`, `winning_variant TEXT`, `created_at TIMESTAMPTZ DEFAULT now()`. CHECK status IN ('active','stopped'). Индекс на `status`.
  - `ALTER TABLE search_events ADD COLUMN IF NOT EXISTS variant TEXT` + индекс `ix_search_events_variant_idx (variant, created_at)` на parent.
  - `downgrade()`: DROP COLUMN variant (+ index), DROP TABLE ab_experiments. Идемпотентность (`IF EXISTS`).
- `app/db/models/ab_experiment.py::ABExperiment` (зеркало таблицы; нужно и для `Base.metadata.create_all` в integration conftest).
- `app/db/models/search_event.py`: поле `variant: Mapped[str | None]`.
- `app/db/queries/ab_experiments.py` (raw SQL в стиле `queries/users.py`):
  - `create_experiment`, `get_by_id`, `get_active_experiments`, `count_active`, `stop_experiment(status='stopped', winning_variant, end_date=today)`.

### 2. `ABTestingService` — `app/services/ab_testing.py` (переписать заготовку)

Конфигурация — `settings.ab_testing: ABTestingConfig` (sticky_hash_buckets=1000, sequential_testing_enabled, significance_level=0.05, min_samples_per_variant=100, max_parallel_experiments=3).

- `assign_variant(user_id: UUID, experiment_id: str, variants: list[str], weights: list[float] | None = None) -> str` — **sync, pure, O(1)**:
  - `bucket = int(hashlib.sha256(f"{user_id}:{experiment_id}".encode()).hexdigest(), 16) % sticky_hash_buckets`
  - кумулятивные веса (default: равные); найти вариант по bucket.
- `get_experiment(session, experiment_id)` — active-only lookup с тонким in-memory TTL-кэшем (60с, инвалидация при stop/create), чтобы `POST /search` не делал SELECT на каждый экспериментальный запрос.
- `compute_daily_metrics(session, experiment_id, date) -> dict[str, dict[str, float]]` — SQL-агрегат по `search_events` за `created_at::date = date` с фильтром `experiment_id`:
  - search-строки: `clicked_doc_id IS NULL AND latency_ms IS NOT NULL`; click-строки: `clicked_doc_id IS NOT NULL`.
  - на variant: `count` (searches), `ctr = clicks/searches`, `mrr = avg(1/position)` по клик-строкам, `no_click_rate = 1 - ctr`, `latency_p95 = percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)`.
- `check_significance(session, experiment_id) -> dict` — sequential testing **mSPRT** (mixture SPRT, нормальная смесь эффектов; НЕ naive t-test):
  - выборка per-search: бинарный click-indicator по variant-строкам.
  - если < min_samples_per_variant → `{"significant": False, "reason": "insufficient_samples", "samples_per_variant": {...}}`.
  - иначе mSPRT-статистика по разнице ctr(control=variant[0]) vs challenger; возврат `{"significant": bool, "winning_variant": str|None, "p_value": float, "samples_per_variant": dict}`; метрика `ab_significance_checked_total.labels(result=...)`.

### 3. Логирование variant в `search_events`

- `app/db/queries/search_events.py`:
  - `insert_search_event(..., variant: str | None = None)` — колонка в INSERT.
  - `insert_click_event(..., variant: str | None = None)` — колонка в INSERT.
  - `archive_partition_to_s3`: добавить `variant` в arrow schema и records.
- `ClickRequest` не менять — variant **пересчитывается** на сервере детерминически из `user_id + experiment_id` (stateless sticky).

### 4. Интеграция в оркестратор и клик-роут

- `SearchOrchestrator.__init__`: новый параметр `ab_testing_service: ABTestingService | None = None`.
- В `search()` сразу после расчёта deadline (до fan-out каналов):
  - если `req.experiment_id` и `req.user_id` и `feature_flags.ab_testing_enabled` и сервис инжектован:
    - `exp = await ab.get_experiment(session, req.experiment_id)` (active only);
    - `req_variant = ab.assign_variant(...)`; `req = req.model_copy(update=params)` (overlay `fusion`, `fusion_alpha`, `rerank`, `personalize`, `diversify` из JSONB варианта — только заданные ключи);
    - `metrics.ab_assignments_total.labels(experiment_id, variant).inc()`.
  - тела каналов не трогаем — variant-параметры уходят в существующие `_fuse`/`_run_pipeline` через заменённый `req`.
- `SearchResponse(..., variant=req_variant)` — поле уже существует.
- В `log_search_event()`: передавать `variant=req_variant`.
- `app/api/routes/search.py::log_click`: при наличии `experiment_id`+`user_id` — пересчитать variant (get_experiment + assign_variant) и передать в `insert_click_event` (в try/except, не ронять ответ).
- `app/api/routes/search.py`: инжектить `get_ab_testing_service()` в оркестратор.
- `app/api/deps.py`: `get_ab_testing_service()` (stateless сервис — lru_cache ok).

### 5. HTTP-эндпоинты — `app/api/routes/experiments.py`

- `POST /experiments` → 201: валидация (`len(variants) >= 2`, `len(weights)==len(variants)`, веса ≥ 0, сумма > 0), `count_active() < max_parallel_experiments` иначе **409 Conflict**. Тело: `{experiment_id, description, variants, weights, start_date}`.
- `GET /experiments/{id}/metrics?date=YYYY-MM-DD` (default: сегодня UTC) → `compute_daily_metrics`.
- `POST /experiments/{id}/stop` → `{winning_variant}`; `status='stopped'`, `end_date=today`; 404 если нет.
- `extra="forbid"` на `ExperimentCreateRequest`, `ExperimentStopRequest`.
- Регистрация роутера в `app/main.py`.

### 6. Cron — `scripts/run_ab_metrics.py`

По образцу `run_partition_management.py`: для каждого active эксперимента — `compute_daily_metrics(сегодня)`, лог, `check_significance`; при significant → `stop_experiment(winning_variant)`, лог, обновить gauge `ab_experiments_active`. Exit codes 0/1.

### 7. Метрики — `app/observability/metrics.py`

- `ab_experiments_active` Gauge.
- `ab_assignments_total` Counter `[experiment_id, variant]`.
- `ab_significance_checked_total` Counter `[result]` ("significant"/"not_significant"/"insufficient_samples").

### 8. Тесты

- `tests/unit/test_ab_testing_test.py`:
  - детерминизм: 100 вызовов `assign_variant` → один вариант;
  - распределение: 10'000 uuid → ≈50/50 и ≈70/30 (веса `[0.7, 0.3]`), допуск ±3%;
  - bucket-boundary корректность кумулятивных весов;
  - `check_significance`: < min_samples → insufficient_samples; синтетические данные с сильным эффектом → significant + winning_variant; без эффекта → not significant;
  - `compute_daily_metrics` с AsyncMock-сессией;
  - превышение max_parallel → доменный exception (route мапит в 409).
- `tests/integration/test_ab_experiment_test.py` (testcontainers PG):
  - POST /experiments → 201; повторный id → 409/unique; превышение parallel → 409;
  - поиск с `experiment_id`+`user_id` → в `search_events` строка с `experiment_id` и `variant`;
  - sticky: два поиска одним user → один variant; POST /search/click → клик-строка с variant;
  - GET metrics → агрегаты; POST stop → status stopped;
  - поиск **без** experiment_id → variant=NULL (regression контракта Critical);
  - учесть fire-and-forget `asyncio.create_task(log_search_event)` — polling с таймаутом ~2с.
- Обновить существующие тесты, если ломаются сигнатуры (variant default=None — не должен ломать).

### 9. Dashboard + конфиг

- `grafana/provisioning/dashboards/prod-ab-testing.json` — 3 панели (ab_assignments_total, ab_experiments_active, ab_significance_checked_total), по образцу critical-*.json.
- `.env.example` — AB_TESTING_* уже есть (PR-00); не трогаем.

### 10. Self-check (правило §0.3.9)

1. `uv run ruff check app/ tests/ scripts/`
2. `uv run ruff format app/ tests/`
3. `uv run mypy app/`
4. `uv run pytest tests/unit/`
5. `uv run pytest tests/integration/test_ab_experiment_test.py` (Docker)
6. `bash scripts/run_dod_check_critical.sh` — нет регрессий Critical.
7. Если доступен стек: `alembic upgrade head` → `downgrade -1` → `upgrade head`.

### 11. Документация статуса

- `analitics/BACKLOG.md` строка PR-03 → `✅ done` (правило §0.3.10).
- Итоговый отчёт: закрытые §ref (ARCHITECT §11.3/§11.4, ROADMAP §5.2.3), девиации: (a) 008 включает `search_events.variant` (перенос из PR-08/009), (b) нумерация PR-10 сдвинется на 009, (c) фикс самоимпорта в заготовке.

## Структура изменений (файлы)

| Файл | Действие |
|---|---|
| `alembic/versions/008_ab_experiments.py` | создать |
| `app/db/models/ab_experiment.py` | создать |
| `app/db/models/__init__.py` | экспорт модели |
| `app/db/models/search_event.py` | +variant |
| `app/db/queries/ab_experiments.py` | создать |
| `app/db/queries/search_events.py` | +variant в insert/archive |
| `app/services/ab_testing.py` | переписать (фикс самоимпорта) |
| `app/api/routes/experiments.py` | создать |
| `app/api/routes/search.py` | click variant recompute + AB dep |
| `app/api/schemas.py` | ExperimentCreate/StopRequest |
| `app/api/deps.py` | get_ab_testing_service |
| `app/search/orchestrator.py` | assign_variant + apply params + logging variant |
| `app/observability/metrics.py` | 3 новые метрики |
| `app/main.py` | include experiments router |
| `scripts/run_ab_metrics.py` | создать |
| `grafana/provisioning/dashboards/prod-ab-testing.json` | создать |
| `tests/unit/test_ab_testing_test.py` | создать |
| `tests/integration/test_ab_experiment_test.py` | создать |
| `analitics/BACKLOG.md` | статус PR-03 |

## Forbidden patterns — контроль

- ❌ хранение assignment в БД (stateless sticky) ✔
- ❌ naive t-test с peeking (mSPRT) ✔
- ❌ variant-параметры при `user_id is None` ✔
- ❌ фичи §6 ROADMAP (bandit, tiering…) — не трогаем ✔
- ❌ ломать контракт `POST /search` без experiment_id — regression-тест ✔
