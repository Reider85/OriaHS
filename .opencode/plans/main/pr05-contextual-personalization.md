# PR-05 — Контекстная персонализация (device / session / time_of_day)

Источник спеки: `analitics/PROD-PROMPTS.md` PR-05 (строки 738–808), ARCHITECT §10.6, ROADMAP §5.2.5.

## Текущее состояние (исследовано)

- Head Alembic: `008_ab_experiments` → миграция **009 свободна** (занимаем `009_user_context_profiles`).
- `SearchContext {device, session_id, time_of_day}` уже существует (`app/api/schemas.py:17-22`), поле `SearchRequest.context` уже добавлено (schemas.py:113).
- Флаг `context_personalization_enabled` уже есть (`app/config.py:344`), но нигде не читается.
- Таблицы `user_context_profiles` и связанного кода нет нигде — greenfield.
- Базовый `PersonalizationService` (PR-01) реализован в `app/services/personalization.py` (EMA с адаптивным α, `personalize_scores` 0.7/0.3, `gdpr_delete`).
- Интеграция персонализации в оркестратор: `app/search/orchestrator.py:281-341`.

### Известные pre-existing баги, которые пересекаются со scope PR-05

1. `get_personalization_service()` (`app/api/deps.py:71-75`) возвращает **класс**, а не инстанс — оркестратор получает класс и рантайм-вызовы методов personalization сломаны. Исправляем: фабрика как `get_ab_testing_service` (session-bound instance). Необходимо, чтобы AC PR-05 (`POST /search` с context) вообще работали.
2. `POST /search/click` (`app/api/routes/search.py:153-158`) вызывает `update_on_click(..., query=req.query)` — такого параметра нет в сигнатуре сервиса (там `embedding_service`). Background task падает с TypeError (глотается except). Исправляем при добавлении contextual-update в этот же путь.
3. Debug `profile_version` в оркестраторе (orchestrator.py:331) читается `getattr(numpy_array, ...)` — всегда 0. Исправляем при переработке debug-блока.
4. Двойной учёт `personalized_searches_total` в оркестраторе (orchestrator.py:289-292, `cold_start="false"` захардкожен) поверх корректного учёта в сервисе. Убираем учёт в оркестраторе.

## Дизайн-решения

- **Один класс вместо второго**: контекстные методы добавляются в существующий `PersonalizationService` (спека допускает «расширение PersonalizationService (или отдельный класс)»). Оркестратор уже инжектит один сервис — DI не усложняется. Поведение соответствует AC.
- **Отдельная таблица** `user_context_profiles` с PK `(user_id, context_key)` (предпочтительный вариант спеки, строки 748–749) — поддерживает pgvector-индексы, не раздувает `users`.
- **Контекстные ключи**: `session:{session_id}` | `morning|afternoon|evening|night` | `desktop|mobile|tablet` | `default`. Приоритет: `session_id` > `time_of_day` > `device` > default. Time-of-day: UTC (через конфиг `context_timezone`).
- **Placeholder-эмбеддинг** для кликнутого документа — паттерн PR-01 (реальный embedding документа отложен; в PR-01 используется `np.random.randn` placeholder). PR-05 повторяет его для контекстных профилей, не расширяя scope.
- **Fallback**: контекстный профиль cold-start (`click_count == 0` или вектор NULL) → default `profile_vector` (ключ `"default"`); и default cold-start → pure fusion (`cold_start=True`).
- **Альфы EMA**: session → `session_profile_alpha=0.5` (быстрое обучение); time-of-day/device → `context_profile_alpha=0.1`. Новая строка в `users` не требуется.

## Изменения по файлам

### 1. Миграция `alembic/versions/009_user_context_profiles.py` (новый)

- `CREATE TABLE user_context_profiles`:
  - `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`
  - `context_key TEXT NOT NULL`
  - `profile_vector VECTOR(1024) NULLABLE` (NULL до первого клика)
  - `click_count INT NOT NULL DEFAULT 0`
  - `profile_version INT NOT NULL DEFAULT 0`
  - `created_at` / `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `PRIMARY KEY (user_id, context_key)`
  - частичный индекс `ix_ucp_session_expiry ON (updated_at) WHERE context_key LIKE 'session:%'` (для expire-cron)
  - trigger `trg_user_context_profiles_updated_at` через shared `set_updated_at()` (как в 006)
- `downgrade()`: DROP TRIGGER + DROP TABLE. Extension `vector` **не** дропаем (её держит `users`).
- Docstring со ссылкой ARCHITECT §10.6, PR-05. Замечание: 010 резервируется за PR-10 (`tenant_search_stats`) из-за занятого PR-03-шным `008_ab_experiments`.

### 2. Модель `app/db/models/user_context_profile.py` (новый)

- `UserContextProfile(Base)`: колонки как в миграции, `__table_args__` = `PrimaryKeyConstraint("user_id", "context_key")` + partial-индекс. Стиль — по образцу `app/db/models/user.py`.
- Зарегистрировать в `app/db/models/__init__.py` (импорт + `__all__`) — иначе autogenerate/метаданные не увидят модель.

### 3. Queries `app/db/queries/user_context_profiles.py` (новый)

Raw-SQL в стиле `app/db/queries/users.py`:
- `get_profile(session, user_id, context_key) -> UserContextProfile | None`
- `upsert_profile(session, user_id, context_key, profile: list[float]) -> None` — `INSERT ... ON CONFLICT (user_id, context_key) DO UPDATE SET profile_vector=:p, profile_version=user_context_profiles.profile_version+1, click_count=user_context_profiles.click_count+1, updated_at=now()`
- `delete_for_user(session, user_id) -> int` — GDPR, возвращает rowcount
- `expire_session_profiles(session, cutoff: datetime) -> int` — `DELETE ... WHERE context_key LIKE 'session:%' AND updated_at < :cutoff`, возвращает rowcount

### 4. Конфиг `app/config.py` — расширить `PersonalizationConfig`

```python
session_profile_alpha: float = Field(default=0.5, ge=0.0, le=1.0)   # быстрое обучение session-профиля
context_profile_alpha: float = Field(default=0.1, ge=0.0, le=1.0)   # time-of-day / device
session_profile_ttl_hours: int = Field(default=24, ge=1)
context_timezone: str = "UTC"
```

`context_personalization_enabled` в `FeatureFlags` уже есть — не трогаем. Обновить `.env.example` (PERSONALIZATION_SESSION_PROFILE_ALPHA и т.д.).

### 5. Сервис `app/services/personalization.py` — расширить `PersonalizationService`

Импорты query-модуля контекстных профилей. Новые методы:

- `resolve_context_key(context: SearchContext | None) -> str | None` — приоритет session > time_of_day > device; статический/чистый, юнит-тестируемый.
- `time_of_day_key(now: datetime) -> Literal[...]` — бакеты UTC: night 0–6, morning 6–12, afternoon 12–18, evening 18–24.
- `get_profile_info(user_id, tenant_id, context: SearchContext | None) -> ProfileInfo` — единая точка для оркестратора. Возвращает dataclass `ProfileInfo(vector: np.ndarray | None, profile_version: int, click_count: int, context_key: str)`. Логика: context None/флаг выключен → default (`users`); иначе ключ из `resolve_context_key`, `get_profile`; если контекстный профиль cold-start → fallback на default (ключ `"default"`); и default cold-start → `vector=None`. При использовании контекстного профиля (не default) — `metrics.contextual_profile_used_total.labels(context_key=...).inc()`. `profile_version` берётся из строки БД (чинит баг №3).
- `personalize_scores(..., profile_vector: np.ndarray | None = None)` — новый **опциональный** kwarg: если передан, использовать его вместо внутреннего `get_profile_vector` (backward-compatible для существующих тестов PR-01).
- `update_on_click_contextual(user_id, tenant_id, clicked_doc_id, embedding_service, context) -> str` — резолвит ключ, EMA: α = `session_profile_alpha` если session, иначе `context_profile_alpha`; placeholder clicked_vector (как в PR-01: `np.random.randn(1024)` + L2-norm); первый клик — вектор напрямую; `upsert_profile`; лог + `profile_vector_updates_total`. Возвращает использованный ключ.
- `expire_session_profiles(session_ttl_hours: int | None = None) -> int` — cutoff = now(UTC) − ttl; вызывает query `expire_session_profiles`; `metrics.session_profiles_expired_total.inc(n)`; лог.
- `gdpr_delete` — расширить: дополнительно `user_context_profiles.delete_for_user(self._session, user_id)` (AC: GDPR удаляет и контекстные профили).

### 6. Схемы `app/api/schemas.py`

- `PersonalizationDebug` (строки 25–30): добавить `context_profile_key: str | None = None`.
- `ClickRequest` (строки 252–266): добавить `context: SearchContext | None = None` (для contextual EMA-обновления по клику).

### 7. Deps `app/api/deps.py`

- `get_personalization_service(session: Depends(get_session))` → возвращает **инстанс** `PersonalizationService(session, get_settings().personalization)` (по образцу `get_ab_testing_service`). Чинит баг №1. Обновить `__all__`-докстринг при необходимости.

### 8. Оркестратор `app/search/orchestrator.py` (блок 281–341)

- Внутри существующего if-блока personalization:
  - Убрать преждевременный `metrics.personalized_searches_total...inc()` (баг №4 — сервис считает сам с настоящим cold_start).
  - Вычислить `context = req.context if self._flags.context_personalization_enabled else None`.
  - `info = await self._personalization.get_profile_info(req.user_id, req.tenant_id, context)`.
  - Передать `profile_vector=info.vector` в `personalize_scores` (при `info.vector is None` — skip скоринга, fused без изменений, как сейчас cold-start).
  - Debug dict: `{"applied": True, "cold_start": info.vector is None, "profile_version": info.profile_version, "context_profile_key": info.context_key}` (чинит баг №3; `"default"` при отсутствии/fallback — выполняет AC).
- Логирование: добавить `context_profile_key` в extra.

### 9. Клик-роут `app/api/routes/search.py` (строки 146–162)

- Чинит баг №2: вызов `update_on_click(user_id=..., tenant_id=..., clicked_doc_id=..., embedding_service=_get_embedding_service())` (убрать `query=`).
- Если `req.context is not None` и `context_personalization_enabled` — в том же background task вызвать `update_on_click_contextual(..., context=req.context)`.

### 10. Метрики `app/observability/metrics.py`

Новый блок «Contextual personalization metrics (PR-05)» после PR-01-блока (строки 180–195):
- `contextual_profile_used_total = Counter(..., ["context_key"])`
- `session_profiles_expired_total = Counter(...)`

### 11. Cron-скрипт `scripts/expire_session_profiles.py` (новый)

По образцу `scripts/run_ab_metrics.py`: docstring, `async def run_expire_session_profiles()`, проверка `feature_flags.context_personalization_enabled` (skip если off), `async_session_factory()`, вызов `PersonalizationService(...).expire_session_profiles()`, лог + метрика, `if __name__ == "__main__"` с `logging.basicConfig` + `asyncio.run`.

### 12. `.env.example`

Добавить `PERSONALIZATION_SESSION_PROFILE_ALPHA`, `PERSONALIZATION_CONTEXT_PROFILE_ALPHA`, `PERSONALIZATION_SESSION_PROFILE_TTL_HOURS`, `PERSONALIZATION_CONTEXT_TIMEZONE` с комментариями.

### 13. Тесты

**`tests/unit/test_context_personalization_test.py`** (новый) — в mock-стиле `test_personalization_test.py` (patch query-функций в `app.services.personalization`):
1. `resolve_context_key`: приоритет session > time_of_day > device; context=None → None.
2. `time_of_day_key`: параметризация по часам (5→night, 7→morning, 13→afternoon, 19→evening, 23→evening/night-граница).
3. `get_profile_info`: (a) context=None → default; (b) контекстный профиль с кликами → его ключ+вектор; (c) контекстный cold-start → fallback default; (d) оба cold-start → vector None, key "default"; (e) метрика `contextual_profile_used_total` с правильным label.
4. `update_on_click_contextual`: α=0.5 для session, 0.1 для device; `upsert_profile` вызван с правильным ключом; возвращает ключ.
5. `expire_session_profiles`: вызов query с cutoff, метрика `session_profiles_expired_total`.
6. `gdpr_delete`: вызывает и `reset_profile`, и `delete_for_user`.
7. `personalize_scores` с переданным `profile_vector` (контекстный) — использует его; без kwarg — прежнее поведение (регрессия PR-01).

**`tests/unit/test_search_orchestrator_test.py`** (расширить) — 1–2 теста: inject mock personalization service; `req.context` + флаг on → `get_profile_info` вызван с context, `resp.personalization.context_profile_key` присутствует; `context_personalization_enabled=False` → context игнорируется.

Существующие `tests/unit/test_personalization_test.py` и `tests/integration/test_personalized_search_test.py` должны остаться зелёными (kwarg обратно-совместим).

### 14. `BACKLOG.md`

Сменить статус PR-05 на `done` (после прохождения всех чеков).

## Верификация (порядок из AGENTS.md: lint → typecheck → test)

1. `uv run ruff check app/ tests/ scripts/` && `uv run ruff format app/ tests/`
2. `uv run mypy app/`
3. `uv run pytest tests/unit/test_context_personalization_test.py tests/unit/test_personalization_test.py tests/unit/test_search_orchestrator_test.py -v`
4. Миграция (идемпотентность): `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` (через docker compose `migrate` или локальный DSN)
5. `uv run pytest tests/unit/ -m "not slow"` — без новых падений
6. `./scripts/run_dod_check_critical.sh` — Critical DoD не сломан (правило 0.3.11)
7. Если поднимается стек: `POST /search` с `personalize=true, context={"device":"mobile"}` → `debug/personalization.context_profile_key="mobile"`; `POST /search/click` с context → session-профиль создаётся.

## Отклонения от спеки (зафиксировать в отчёте)

- Класс `ContextualPersonalizationService` не создаётся отдельно — методы расширяют `PersonalizationService` (спека: «или отдельный класс»).
- Миграция нумеруется `009` (как в спеке), но `008` занят PR-03 (`ab_experiments`) — PR-10 сместится на 010.
- Embedding кликнутого документа — placeholder `np.random.randn` (паттерн PR-01, реальный embedding отложен); в scope PR-05 не входит.
- Включены 4 minimal enabling-fix (deps-инстанс, клик-route сигнатура, profile_version debug, двойной учёт метрики) — без них AC PR-05 недостижимы.

## Файлы (сводка)

| Файл | Действие |
|---|---|
| `alembic/versions/009_user_context_profiles.py` | новый |
| `app/db/models/user_context_profile.py` | новый |
| `app/db/models/__init__.py` | правка (регистрация) |
| `app/db/queries/user_context_profiles.py` | новый |
| `app/config.py` | правка (4 поля PersonalizationConfig) |
| `.env.example` | правка |
| `app/services/personalization.py` | правка (контекстные методы, ProfileInfo, gdpr cascade) |
| `app/api/schemas.py` | правка (PersonalizationDebug, ClickRequest) |
| `app/api/deps.py` | правка (инстанс-фабрика) |
| `app/search/orchestrator.py` | правка (контекстный профиль, debug, метрики) |
| `app/api/routes/search.py` | правка (клик: фикс сигнатуры + contextual update) |
| `app/observability/metrics.py` | правка (2 счётчика) |
| `scripts/expire_session_profiles.py` | новый |
| `tests/unit/test_context_personalization_test.py` | новый |
| `tests/unit/test_search_orchestrator_test.py` | расширение |
| `BACKLOG.md` | правка статуса |
