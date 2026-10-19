# BUGFIXES-PROMPTS — Промпты для ИИ-агента (Claude Code) по исправлению дефектов, выявленных при ревью реализации MVP-PROMPTS.md и CRITICAL-PROMPTS.md

> **Источник**: Ревизия репозитория `Reider85/OriaHS` (HEAD на момент проверки)
> **Предусловие**: Реализованы промпты `MVP-PROMPTS.md` P-00..P-17 и `CRITICAL-PROMPTS.md` C-00..C-14
> **Стек**: Python 3.11+ (FastAPI, SQLAlchemy 2.x async), PostgreSQL 16, Qdrant 1.x, Redis 7
> **Целевой агент**: Claude Code
> **Стиль промптов**: spec-driven (Context / Goal / Constraints / References / Acceptance Criteria / Forbidden patterns / Suggested files)
> **Гранулярность**: 1 промпт = 1 задача (~15–90 минут работы агента)

---

## 0. Как пользоваться этим документом

### 0.1. Порядок выполнения

Промпты пронумерованы `B-00` … `B-12` и сгруппированы по приоритету:

| Приоритет | Диапазон | Описание |
|---|---|---|
| **P0 — Critical** | B-00, B-01, B-02 | Блокируют production-readiness; чинить первыми |
| **P1 — High** | B-03, B-04, B-05, B-06 | Нарушают acceptance criteria конкретных промптов |
| **P2 — Medium** | B-07, B-08, B-09 | Латентные баги / неполные DoD-тесты |
| **P3 — Low / Polish** | B-10, B-11, B-12 | Косметика, наблюдаемость, консистентность |

### 0.2. Карта промптов

| ID | Приоритет | Затронутый промпт | Компонент | Ожидаемый артефакт |
|----|---|---|---|---|
| B-00 | P0 | P-07 | `POST /index` throttle | Рабочий `get_throttle_service()` |
| B-01 | P0 | C-03 | Circuit breaker | Auto-recovery `open → half_open` |
| B-02 | P0 | C-14 | E2E Critical DoD | Парсящийся + корректный test-модуль |
| B-03 | P1 | C-02 | Weighted fusion | Single-element normalization → 1.0 |
| B-04 | P1 | C-07 | Nightly eval | `func` import, `block_release`, worst-queries, test mock |
| B-05 | P1 | C-08 | `wait_for_index` | `Cache-Control: no-store` + rate-limit stub |
| B-06 | P1 | C-11 / C-12 | Throttle metrics | `outbox_throttled_total.inc()` + integration test |
| B-07 | P2 | — | `app/services/qdrant.py` | `import asyncio` |
| B-08 | P2 | P-17 | MVP DoD tests | Реальные DoD 3 / DoD 5 / NFR scaling / port fix |
| B-09 | P2 | C-13 | Grafana Critical | Alerts + `schemaVersion` + `${DS_PROMETHEUS}` |
| B-10 | P3 | C-12 | Metrics tests | Параметризация на все 15 Critical-метрик |
| B-11 | P3 | C-04 | Speculative rerank | `remaining_count` + `total_rerank_ms` в логах |
| B-12 | P3 | несколько | Polish batch | См. тело промпта |

### 0.3. Сводка проверки

- **MVP-PROMPTS**: 16/18 ✅, 2 ⚠️ (P-07, P-17)
- **CRITICAL-PROMPTS**: 8/15 ✅, 6 ⚠️ (C-02, C-03, C-07, C-08, C-11, C-12, C-13), 1 ❌ (C-14)
- **Итого**: 24/33 полностью, 8 частично, 1 отсутствует

---

## 1. P0 — Critical (блокируют production)

### B-00 — `POST /index`: throttle session-factory bug

**Context**: `get_throttle_service()` в `app/api/routes/index.py:35` вызывает `get_session()` (async-генератор) и трактует результат как session-factory. При первом же `POST /index` метод `OutboxThrottle.get_pending_count()` выполняет `async with self._session_factory() as session:` над async-generator'ом → `TypeError: 'async_generator' object is not callable`. Баг замаскирован тем, что slow-интеграционные тесты требуют Docker и не запускаются в CI без него. В реальном окружении **каждый** `POST /index` вернёт 500 вместо 201.

**Goal**: Заставить `POST /index` корректно проходить через throttle-проверку и возвращать 201 (или 202 при throttled).

**Constraints**:
- Не менять публичный контракт `IndexResponse`.
- Использовать тот же паттерн session-factory, что в `app/reconciler/worker.py:29,66`, `app/reconciler/digest.py:27,48`, `app/eval/nightly.py:22,62`.
- Throttle должен остаться опциональным (feature flag `THROTTLE_ENABLED`).

**References**:
- `MVP-PROMPTS.md` P-07 (Acceptance Criteria)
- `CRITICAL-PROMPTS.md` C-11
- `app/db/session.py:36` (`async_session_factory`)
- `app/db/session.py:44-47` (`get_session` — async generator)
- `app/api/routes/index.py:29-43`
- `app/services/throttle.py:60`

**Acceptance Criteria**:
- [ ] `app/api/routes/index.py` импортирует `async_session_factory` (а не `get_session`) для передачи в `OutboxThrottle`.
- [ ] `POST /index` с пустым outbox возвращает 201 `{"status":"queued","throttled":false}`.
- [ ] `POST /index` при 60k pending возвращает 202 `{"status":"throttled","throttled":true}`.
- [ ] Интеграционный тест `test_index_route_throttle_202` (seed 60k pending → POST → 202) добавлен в `tests/integration/test_index_route_test.py`.
- [ ] `pytest tests/integration/test_index_route_test.py -v -m slow` проходит (с Docker).

**Forbidden patterns**:
- ❌ Не вызывать `get_session()` как фабрику сессий (это async-генератор).
- ❌ Не создавать новый `async_sessionmaker` внутри route handler.
- ❌ Не отключать throttle через `ThrottleDep = None`.

**Suggested files**:
- `app/api/routes/index.py`
- `app/services/throttle.py` (проверить сигнатуру конструктора)
- `tests/integration/test_index_route_test.py` (новый тест)

---

### B-01 — Circuit breaker: auto-recovery `open → half_open`

**Context**: `RerankerCircuitBreaker.call()` в `app/reranker/circuit_breaker.py` выбрасывает `CircuitBreakerOpen` **до** вызова `_check_and_transition()`. В результате переход `open → half_open` после cooldown'а недостижим из публичного API. В проде предохранитель открывается один раз и остаётся в этом состоянии до перезапуска процесса. Unit-тесты проходят только потому, что вручную вызывают `await breaker._check_and_transition()` после sleep'а.

**Goal**: Circuit breaker должен автоматически переходить `open → half_open` по истечении cooldown без ручного вмешательства.

**Constraints**:
- Не использовать `circuitbreaker` библиотеку.
- Lazy-проверка cooldown (не background-timer).
- Сохранить все существующие метрики и тесты.

**References**:
- `CRITICAL-PROMPTS.md` C-03 (Acceptance Criteria: «After 60 sec cooldown → state()=="half_open"»)
- `app/reranker/circuit_breaker.py:82-122` (`call()`)
- `app/reranker/circuit_breaker.py:172-176` (`_check_and_transition`, `open → half_open`)
- `tests/unit/test_circuit_breaker_test.py:88-110` (`test_half_open_state_probes_one_call`)

**Acceptance Criteria**:
- [ ] В начале `call()` (до `if self._state == "open": raise`) вызывается `await self._check_and_transition()`.
- [ ] Тест `test_breaker_auto_recovers_after_cooldown` (без ручного `_check_and_transition`) добавлен и проходит: открыть → `sleep(cooldown + 1s)` → следующий `call()` инвоцирует `fn` (probe).
- [ ] Существующие тесты `test_half_open_state_probes_one_call`, `test_concurrent_calls_error_rate` по-прежнему зелёные.
- [ ] Метрика `circuit_breaker_state` корректно отражает переход `open(2) → half_open(1) → closed(0)`.

**Forbidden patterns**:
- ❌ Не использовать `threading.Timer` / `asyncio.create_task` для фонового сброса.
- ❌ Не удалять `_check_and_transition` (он остаётся методом).
- ❌ Не инкрементировать `circuit_breaker_opened_total` при каждом `call()` в `open`.

**Suggested files**:
- `app/reranker/circuit_breaker.py`
- `tests/unit/test_circuit_breaker_test.py`

---

### B-02 — `tests/e2e/test_critical_dod.py`: синтаксис + логика

**Context**: Файл `tests/e2e/test_critical_dod.py` (786 строк) **не парсится** — `IndentationError` на строках 222-223: блок `except Exception as e:` не имеет тела, а `print(...)` стоит на уровне `except`. Из-за этого **ни один** Critical DoD-тест не выполняется. Дополнительно в файле обнаружены логические ошибки: проверка метрики через несуществующий label `state="open"` (на самом деле state — это значение gauge, а не label); patch `QdrantService.search` при отсутствии такого метода; `response["hits"]` на pydantic-модели.

**Goal**: Файл парсится, импортируется, все 6 DoD-тестов запускаются и проходят (с `docker-compose up`).

**Constraints**:
- Не переписывать файл с нуля — точечные фиксы существующего.
- Сохранить имена тестов и их DoD-нумерацию (DoD 1..6).
- Для DoD 4 (Qdrant kill) патчить `app.search.orchestrator.vector_search`, а не `QdrantService.search`.

**References**:
- `CRITICAL-PROMPTS.md` C-14 (Acceptance Criteria)
- `tests/e2e/test_critical_dod.py:222-223` (IndentationError)
- `tests/e2e/test_critical_dod.py:215, 253, 255` (metric label bug)
- `tests/e2e/test_critical_dod.py:454-459, 768-773` (patch target bug)
- `tests/e2e/test_critical_dod.py:360` (`response["hits"]` на pydantic)
- `tests/e2e/conftest.py:240-257` (`truncate_critical_tables` scope)
- `tests/integration/test_degraded_mode_test.py:84` (правильный patch target)

**Acceptance Criteria**:
- [ ] `python -c "import ast; ast.parse(open('tests/e2e/test_critical_dod.py').read())"` → без ошибок.
- [ ] `pytest --collect-only tests/e2e/test_critical_dod.py` собирает все 6+ тест-функций.
- [ ] Проверка `circuit_breaker_state` использует значение gauge (`2` = open, `1` = half_open, `0` = closed), а не label `state=...`.
- [ ] Patch degraded-mode указывает на `app.search.orchestrator.vector_search` (или `app.search.vector.vector_search`).
- [ ] Доступ к хитам — через атрибут `response.hits` (pydantic), не `response["hits"]`.
- [ ] Fixture `truncate_critical_tables` переведена в `scope="function"` (TRUNCATE между тестами, а не один раз в конце session).
- [ ] `pytest tests/e2e/test_critical_dod.py -v` (с Docker) → все тесты зелёные или корректно skip-нутые (`@pytest.mark.gpu`, `@pytest.mark.skip` для DoD 6 pushdown latency).
- [ ] `bash scripts/run_dod_check_critical.sh` → exit 0.

**Forbidden patterns**:
- ❌ Не использовать `response["hits"]` / `response["degraded"]` — только атрибуты.
- ❌ Не патчить несуществующие методы `QdrantService.search`.
- ❌ Не использовать `state="open"` как label в PromQL-строке.
- ❌ Не оставлять `scope="session"` для truncate-fixture.

**Suggested files**:
- `tests/e2e/test_critical_dod.py`
- `tests/e2e/conftest.py`
- `scripts/run_dod_check_critical.sh` (если нужны корректировки NFR-проверок)
- `scripts/run_nfr_load_critical.py` (поправить `embedding_cache_hit_rate` → `embedding_cache_hits_total / embedding_cache_requests_total`)

---

## 2. P1 — High (нарушают acceptance criteria)

### B-03 — Weighted fusion: single-element normalization

**Context**: `weighted_fuse([(id1, 0.5)], [])` возвращает `[(id1, 0.0)]` вместо `[(id1, 1.0)]` по спеке. Когда в канале один документ, `lo == hi` → `rng = 0.0` → fallback `rng = 1.0` → `(s - lo)/rng = 0.0`. Спецификация C-02 требует: «нормализация одного элемента даёт 1.0». Тест `test_both_channels_same_doc` намеренно фиксирует `0.0`, документируя отклонение, но acceptance criterion C-02 не выполнен.

**Goal**: Single-element канал нормализуется к `1.0`; документ-топ в обоих каналах даёт `score ≈ alpha * 1.0 + (1-alpha) * 1.0 = 1.0`.

**Constraints**:
- Не использовать z-score.
- Не менять alpha-семантику (alpha — вес лексического канала).
- RRF остаётся дефолтной fusion-стратегией.

**References**:
- `CRITICAL-PROMPTS.md` C-02 (Acceptance Criteria: «`weighted_fuse([], [(id1, 0.9)])` → `[(id1, 1.0)]`»)
- `app/search/fusion.py:50-97` (`weighted_fuse`)
- `app/search/fusion.py:84` (`rng = hi - lo or 1.0`)
- `tests/unit/test_rrf_fusion_test.py` (`TestWeightedFuse.test_both_channels_same_doc`)

**Acceptance Criteria**:
- [ ] `weighted_fuse([], [(id1, 0.9)], alpha=0.5)` → `[(id1, 1.0)]`.
- [ ] `weighted_fuse([(id1, 0.5)], [], alpha=0.5)` → `[(id1, 1.0)]`.
- [ ] `weighted_fuse([(id1, 0.3), (id2, 0.9)], [], alpha=0.5)` → `[(id2, 1.0), (id1, 0.0)]` (multi-element нормализация не меняется).
- [ ] `weighted_fuse(lex, vec, 0.3)` и `weighted_fuse(vec, lex, 0.7)` дают тот же порядок doc_id (symmetry).
- [ ] Тест `test_both_channels_same_doc` обновлён: ожидает `score == 1.0` (с `pytest.approx`).
- [ ] Тест `test_single_element_normalizes_to_one` добавлен.

**Forbidden patterns**:
- ❌ Не менять multi-element min-max логику.
- ❌ Не возвращать `0.5` вместо `1.0` для single-element.
- ❌ Не добавлять глобальный min/max.

**Suggested files**:
- `app/search/fusion.py` (метод `_norm` внутри `weighted_fuse`)
- `tests/unit/test_rrf_fusion_test.py`

---

### B-04 — Nightly eval: `func` import, `block_release`, worst-queries, test mock

**Context**: В модуле `app/eval/` накопилось несколько дефектов: (1) `get_latest_run_timestamp` в `app/db/queries/eval.py:98` использует `func.max` без `from sqlalchemy import func` → `NameError` при вызове; (2) `EvalConfig` не содержит поля `block_release` (спека говорит «если regression и `block_release=True` — exit 1»); (3) `_log_worst_queries` логирует первые 3 запроса вместо худших 3 по recall; (4) интеграционный тест мокает несуществующий метод `eval_job._load_dataset` вместо `app.eval.nightly.load_dataset` → тест проходит случайно.

**Goal**: Nightly eval корректно отрабатывает, регрессия детектится, CI-gate работает.

**Constraints**:
- Не менять формулы `recall@10`, `ndcg@10`, `mrr`.
- Не удалять старые `eval_results` строки.
- Thresholds берутся из `EvalConfig`.

**References**:
- `CRITICAL-PROMPTS.md` C-07 (Constraints, Acceptance Criteria)
- `app/db/queries/eval.py:98` (`func.max` без импорта)
- `app/config.py` (`EvalConfig`)
- `app/eval/nightly.py:333-344` (`_log_worst_queries`)
- `tests/integration/test_nightly_eval_test.py` (мок `eval_job._load_dataset`)
- `app/db/models/eval_dataset.py` (`unique_together` Django-ism)

**Acceptance Criteria**:
- [ ] `from sqlalchemy import func` (или `from sqlalchemy.sql import func`) добавлен в `app/db/queries/eval.py`.
- [ ] `get_latest_run_timestamp` вызывается без `NameError`.
- [ ] `EvalConfig` содержит `block_release: bool = Field(True)`; `nightly.py._main` проверяет `if report.regression_detected and settings.eval.block_release: sys.exit(1)`.
- [ ] `_log_worst_queries` трекает per-query recall (dict `query_id → recall`) и логирует топ-3 запроса с минимальным recall (включая `recall=0`).
- [ ] Интеграционный тест мокает `app.eval.nightly.load_dataset` (через `monkeypatch.setattr("app.eval.nightly.load_dataset", mock_loader)`), а не `eval_job._load_dataset`.
- [ ] `EvalDataset.__table_args__` заменён с Django-ism `unique_together` на `sa.UniqueConstraint("name", "version", name="uq_eval_datasets_name_version")` (или удалён, т.к. миграция уже создаёт constraint).
- [ ] `pytest tests/integration/test_nightly_eval_test.py -v -m slow` проходит.

**Forbidden patterns**:
- ❌ Не использовать `sklearn.ndcg_score`.
- ❌ Не удалять старые `eval_results`.
- ❌ Не добавлять случайные стратегии (только `rrf`, `weighted`, `weighted+rerank`).
- ❌ Не мокать несуществующие атрибуты в тестах.

**Suggested files**:
- `app/db/queries/eval.py`
- `app/config.py` (`EvalConfig`)
- `app/eval/nightly.py`
- `app/db/models/eval_dataset.py`
- `tests/integration/test_nightly_eval_test.py`

---

### B-05 — `wait_for_index`: `Cache-Control: no-store` + rate-limit stub

**Context**: Эндпоинт `GET /index/status/{token}` в `app/api/routes/index.py:84-117` не выставляет заголовок `Cache-Control: no-store`, но тест `test_cache_control_header` его проверяет → тест упадёт. Также не реализован rate-limit 10 req/s per IP (constraint из C-08, строка 865).

**Goal**: `wait_for_index` корректно предотвращает кэширование и ограничивает частоту опроса.

**Constraints**:
- Rate-limit — stub (Redis-counter или `slowapi`), не production-grade WAF.
- Заголовок выставляется только на `/index/status/*`, не глобально.

**References**:
- `CRITICAL-PROMPTS.md` C-08 (Constraints: «`Cache-Control: no-store`», «Rate-limit: 10 req/s per IP»)
- `app/api/routes/index.py:84-117`
- `tests/integration/test_wait_for_index_test.py:142-155` (`test_cache_control_header`)
- `app/api/middleware/` (директория для нового middleware, если нужно)

**Acceptance Criteria**:
- [ ] `GET /index/status/{token}` возвращает заголовок `Cache-Control: no-store`.
- [ ] Тест `test_cache_control_header` проходит (assert `resp.headers["cache-control"] == "no-store"`).
- [ ] Rate-limit middleware (или `slowapi.Limiter`) ограничивает `GET /index/status/*` до 10 req/s per IP; превышение → 429 `{"detail":"Rate limit exceeded"}`.
- [ ] Тест `test_wait_for_index_rate_limit_429` добавлен (10 быстрых запросов → 11-й возвращает 429).
- [ ] Rate-limit не влияет на `POST /index`, `DELETE /index`, `POST /search`.

**Forbidden patterns**:
- ❌ Не кэшировать ответ `wait_for_index` ни в каком слое.
- ❌ Не использовать long-polling / WebSocket.
- ❌ Не возвращать полный outbox-record (только `token` + `status`).

**Suggested files**:
- `app/api/routes/index.py`
- `app/api/middleware/rate_limit.py` (новый)
- `app/main.py` (register middleware)
- `tests/integration/test_wait_for_index_test.py`

---

### B-06 — Throttle: `outbox_throttled_total` + integration test

**Context**: Метрика `outbox_throttled_total` объявлена в `app/observability/metrics.py:199`, но `.inc()` нигде не вызывается. Спека C-11 требует: «После `POST /index` при throttled — `outbox_throttled_total` инкрементирован». Также отсутствует интеграционный тест сценария 60k pending → 202.

**Goal**: Метрика корректно инкрементируется; интеграционный тест покрывает throttle-path.

**Constraints**:
- Инкремент только при реальном throttle (не при каждом `POST /index`).
- Не дублировать инкремент в нескольких местах.

**References**:
- `CRITICAL-PROMPTS.md` C-11 (Acceptance Criteria)
- `CRITICAL-PROMPTS.md` C-12 (метрика `outbox_throttled_total`)
- `app/observability/metrics.py:199`
- `app/services/indexing.py:124-144`
- `app/services/throttle.py`

**Acceptance Criteria**:
- [ ] `outbox_throttled_total.inc()` вызывается в `IndexingService.create()` (или `OutboxThrottle.should_throttle()`) когда `should_throttle()` возвращает `True`.
- [ ] `POST /index` при 60k pending → 202 + `/metrics` содержит `outbox_throttled_total 1` (после одного запроса).
- [ ] `POST /index` при 1000 pending → 201 + `outbox_throttled_total` не растёт.
- [ ] Интеграционный тест `test_index_throttle_returns_202_and_increments_metric` (seed 60k pending → POST → assert 202 + metric incremented) добавлен в `tests/integration/test_index_route_test.py`.
- [ ] `outbox_reindex_triggered_total` инкрементируется при 150k pending (уже работает — проверить).

**Forbidden patterns**:
- ❌ Не инкрементировать `outbox_throttled_total` при каждом `POST /index`.
- ❌ Не возвращать 503.
- ❌ Не делать `SELECT count(*)` на каждый POST (кэш в Redis 5s TTL — уже есть).

**Suggested files**:
- `app/services/indexing.py`
- `app/services/throttle.py`
- `tests/integration/test_index_route_test.py`

---

## 3. P2 — Medium (латентные баги / неполные DoD)

### B-07 — `app/services/qdrant.py`: missing `import asyncio`

**Context**: `QdrantService.check_health()` использует `asyncio.wait_for(...)`, но модуль не содержит `import asyncio`. Метод сейчас dead code (нет вызывающих из `app/`), но при включении degraded-mode path (C-09) он вызовет `NameError: name 'asyncio' is not defined`.

**Goal**: Метод `check_health()` вызывается без `NameError`.

**Constraints**:
- Однострочный фикс.
- Не менять логику метода.

**References**:
- `app/services/qdrant.py:75` (`asyncio.wait_for`)
- `CRITICAL-PROMPTS.md` C-09 (degraded mode health check)

**Acceptance Criteria**:
- [ ] `import asyncio` добавлен в начало `app/services/qdrant.py`.
- [ ] `python -c "from app.services.qdrant import QdrantService; import inspect; print(inspect.getsource(QdrantService.check_health))"` → без ошибок.
- [ ] Тест `test_qdrant_check_health_returns_bool` (mock AsyncQdrantClient.get_collection → True / raises → False) добавлен или обновлён.

**Forbidden patterns**:
- ❌ Не заменять `asyncio.wait_for` на `asyncio.timeout` (если только не удаление Python <3.11 support).
- ❌ Не убирать timeout.

**Suggested files**:
- `app/services/qdrant.py`
- `tests/unit/` (если нужен новый тест)

---

### B-08 — MVP DoD tests: DoD 3 / DoD 5 / NFR / conftest port

**Context**: `tests/e2e/test_mvp_dod.py` содержит несколько неполных тестов: (1) DoD 3 (`test_dod_3_reconciler_recovery`) — stub без реальной остановки Qdrant, ссылается на неопределённые `qdrant_client` и `settings`; (2) DoD 5 (`test_dod_5_dead_letter_digest`) — `pytest.skip` вместо проверки сценария 20 попыток → dead → digest; (3) NFR-измерения в `run_dod_check.sh` уменьшены до 20 запросов вместо 1000; (4) `conftest.py:54` — `QdrantContainer.get_exposed_port(6379)` (нужно 6333).

**Goal**: DoD 3 и DoD 5 реально проверяют сценарии; NFR-замеры масштабны; conftest корректно подключается к Qdrant.

**Constraints**:
- Использовать `testcontainers` `container.stop()` / `container.start()` для DoD 3.
- Для DoD 5 — не полагаться на случайность; инжектить `embedding_model='nonexistent'` через SQL.
- NFR: минимум 1000 запросов для p99 (статистическая значимость).

**References**:
- `MVP-PROMPTS.md` P-17 (DoD 3, DoD 5, NFR)
- `tests/e2e/test_mvp_dod.py:137-150` (DoD 3 stub)
- `tests/e2e/test_mvp_dod.py` (DoD 5 skip)
- `tests/e2e/conftest.py:54` (port bug)
- `scripts/run_dod_check.sh` (NFR scaling)

**Acceptance Criteria**:
- [ ] `conftest.py:54`: `get_exposed_port(6379)` → `get_exposed_port(6333)`.
- [ ] DoD 3: `test_dod_3_reconciler_recovery` останавливает Qdrant-container (`qdrant_container.stop()`), запускает reconciler, проверяет что outbox-запись переходит в `failed`/`dead`, затем `qdrant_container.start()`, повторный reconciler-run → `done`.
- [ ] DoD 5: `test_dod_5_dead_letter_digest` инжектит документ с `embedding_model='nonexistent'` (через SQL `UPDATE`), запускает reconciler 20 раз (или до `status='dead'`), запускает `DigestWorker.run_once()`, проверяет строку в `search_outbox_dead_digest` с `error_type='embedding_failed'` и `sample_doc_ids` содержит UUID.
- [ ] `scripts/run_dod_check.sh` NFR: latency p99 на 1000 запросах; throughput на 500 `/index` (≥500 doc/min) и 100 RPS `/search`; lag ≤ 30s; SLA 99% за 10 мин.
- [ ] `pytest tests/e2e/test_mvp_dod.py -v -m slow` (с Docker) → все 6 DoD зелёные.
- [ ] `bash scripts/run_dod_check.sh` → exit 0.

**Forbidden patterns**:
- ❌ Не использовать `pytest.skip` вместо реальной проверки.
- ❌ Не ссылаться на неопределённые переменные (`qdrant_client`, `settings` без импорта).
- ❌ Не уменьшать NFR-выборку ниже 1000 запросов.

**Suggested files**:
- `tests/e2e/conftest.py`
- `tests/e2e/test_mvp_dod.py`
- `scripts/run_dod_check.sh`

---

### B-09 — Grafana Critical dashboards: alerts + schemaVersion + datasource var

**Context**: 4 Critical-дашборда (`critical-reranker.json`, `critical-eval-regression.json`, `critical-pushdown.json`, `critical-circuit-breaker.json`) содержат несколько нарушений спеки C-13: (1) нет alert-правил («circuit_breaker_open > 60s», «recall drop > 2%»); (2) нет `schemaVersion`; (3) нет `${DS_PROMETHEUS}` template-переменной; (4) `critical-pushdown.json` Panel 3 показывает «Results Breakdown» вместо «Latency comparison: push-down vs no-push-down» (требует метрику `vector_search_latency_ms{pushdown=true|false}`); (5) `critical-eval-regression.json` Panel 4 показывает «Eval Runs & Regressions» вместо «Last run timestamp» (требует метрику `eval_last_run_timestamp`).

**Goal**: Дашборды соответствуют спеке C-13 и корректно импортируются в Grafana 9+.

**Constraints**:
- Alerts — в формате Grafana 9+ unified alerting (можно в отдельном `rules.yaml` или в поле `alert` панели).
- `${DS_PROMETHEUS}` — template variable типа datasource.
- Новые метрики (`vector_search_latency_ms`, `eval_last_run_timestamp`) добавить в `app/observability/metrics.py` и покрыть инкрементами.

**References**:
- `CRITICAL-PROMPTS.md` C-13 (Acceptance Criteria, Forbidden patterns)
- `grafana/provisioning/dashboards/critical-*.json` (4 файла)
- `grafana/provisioning/dashboards/dashboards.yml`
- `app/observability/metrics.py`

**Acceptance Criteria**:
- [ ] Все 4 `critical-*.json` содержат `"schemaVersion": 39` (или новее).
- [ ] Все 4 дашборда имеют template variable `${DS_PROMETHEUS}` типа `datasource` (query `prometheus`), targets ссылаются на `$DS_PROMETHEUS`.
- [ ] Alert rule «circuit_breaker_open > 60s» в `critical-circuit-breaker.json` (Pending → Firing): условие `max_over_time(circuit_breaker_state{component="reranker"}[1m]) == 2` > 60s.
- [ ] Alert rule «recall drop > 2%» в `critical-eval-regression.json`: условие `(eval_recall_at_10{strategy="rrf"} - eval_recall_at_10{strategy="rrf"} offset 1d) > 0.02` → Pending → Firing.
- [ ] `critical-pushdown.json` Panel 3: «Latency comparison: push-down vs no-push-down» с двумя series `vector_search_latency_ms{pushdown="true"}` и `vector_search_latency_ms{pushdown="false"}`.
- [ ] `critical-eval-regression.json` Panel 4: «Last run timestamp» с метрикой `eval_last_run_timestamp` (Gauge).
- [ ] Новые метрики `vector_search_latency_ms` (Histogram, label `pushdown: bool`) и `eval_last_run_timestamp` (Gauge) добавлены в `app/observability/metrics.py`.
- [ ] `vector_search_latency_ms` инкрементируется в `app/search/vector.py` (measure latency, `.observe(latency_ms, labels={"pushdown": str(bool(pushdown_ids))})`).
- [ ] `eval_last_run_timestamp` set в `app/eval/nightly.py` после каждого прогона (`.set(time.time())`).
- [ ] `docker-compose up` → Grafana показывает все 4 Critical-дашборда без ошибок импорта.

**Forbidden patterns**:
- ❌ Не хардкодить URL Prometheus в JSON.
- ❌ Не использовать deprecated Grafana JSON schema (v1).
- ❌ Не добавлять `$tenant_id` free-input panel variable (high-cardinality).

**Suggested files**:
- `grafana/provisioning/dashboards/critical-reranker.json`
- `grafana/provisioning/dashboards/critical-eval-regression.json`
- `grafana/provisioning/dashboards/critical-pushdown.json`
- `grafana/provisioning/dashboards/critical-circuit-breaker.json`
- `app/observability/metrics.py`
- `app/search/vector.py`
- `app/eval/nightly.py`

---

## 4. P3 — Low / Polish

### B-10 — Metrics tests: параметризация на все 15 Critical-метрик

**Context**: `TestAllMetricsExposed.test_metric_present` в `tests/unit/test_metrics_test.py:241-266` параметризует только 12 из 22 метрик (7 MVP + 5 Critical). Отсутствуют в параметризации: `pushdown_selectivity`, `eval_recall_at_10`, `eval_ndcg_at_10`, `eval_mrr`, `eval_runs_total`, `eval_regression_detected_total`, `outbox_pending_count`, `outbox_throttled_total`, `outbox_reindex_triggered_total`, `search_degraded_total`, `search_partial_total`.

**Goal**: Тест покрывает все объявленные метрики.

**Constraints**:
- Не дублировать тест-логику — расширить `@pytest.mark.parametrize` список.

**References**:
- `CRITICAL-PROMPTS.md` C-12
- `tests/unit/test_metrics_test.py:241-266`
- `app/observability/metrics.py`

**Acceptance Criteria**:
- [ ] `test_metric_present` параметризован на все 22+ метрики (7 MVP + 15 Critical + extras).
- [ ] Тест проверяет, что каждая метрика присутствует в singleton `METRICS` и доступна в `/metrics` выводе.
- [ ] `pytest tests/unit/test_metrics_test.py -v` → все кейсы зелёные.
- [ ] Docstring `app/observability/__init__.py` обновлён (актуальное количество collectors).

**Forbidden patterns**:
- ❌ Не хардкодить значения метрик в тесте (только наличие).

**Suggested files**:
- `tests/unit/test_metrics_test.py`
- `app/observability/__init__.py`

---

### B-11 — Speculative rerank: логирование `remaining_count` + `total_rerank_ms`

**Context**: `app/search/speculative.py` логирует `speculative_count` (line 95), но не логирует `remaining_count` (требование C-04). Fallback-лог (lines 188-191) использует `total_rerank_ms = getattr(rerank_results[0], '_inference_ms', 0)`, но `RerankResult` — pydantic-модель без атрибута `_inference_ms` → всегда `0`.

**Goal**: Логи содержат `remaining_count` и реальный `total_rerank_ms`.

**Constraints**:
- `remaining_count` = количество doc_id, которые пришлось доранжировать после speculative (т.е. не попали в speculative top-N).
- `total_rerank_ms` — сумма `inference_ms` всех rerank-вызовов.
- Не менять сигнатуру `SpeculativeReranker.run`.

**References**:
- `CRITICAL-PROMPTS.md` C-04 (Acceptance Criteria: «Лог содержит speculative_count и remaining_count»)
- `app/search/speculative.py:95, 188-191`
- `app/reranker/schemas.py` (`RerankResult`)
- `app/reranker/service.py:122-130` (inference_ms уже логируется здесь)

**Acceptance Criteria**:
- [ ] `RerankResult` содержит поле `inference_ms: float | None = None` (или `RerankResult` расширяется, или `SpeculativeReranker` трекает время сам).
- [ ] Лог в `_start_speculative` содержит `speculative_count` (число кандидатов из lexical top-N) и `remaining_count` (число уникальных doc_id из vector, не попавших в speculative).
- [ ] Fallback-лог содержит реальный `total_rerank_ms` (сумма `inference_ms` всех rerank-вызовов, не `getattr` на несуществующем атрибуте).
- [ ] Тест `test_speculative_rerank_logging_fields` проверяет, что лог содержит оба поля с числовыми значениями.

**Forbidden patterns**:
- ❌ Не использовать `getattr(obj, '_inference_ms', 0)` для pydantic-моделей.

**Suggested files**:
- `app/search/speculative.py`
- `app/reranker/schemas.py`
- `tests/unit/test_speculative_rerank_test.py`

---

### B-12 — Polish batch: консистентность + мелкие дефекты

**Context**: Набор мелких дефектов, не блокирующих функциональность, но нарушающих консистентность кодовой базы или отдельные acceptance criteria.

**Goal**: Устранить мелкие дефекты пакетом.

**Constraints**:
- Каждый пункт можно выполнить независимым коммитом.
- Не менять публичные API.

**References** (по пунктам):

**Пункт 1 — P-15 logger inconsistency**:
- `app/reconciler/digest.py:29` использует `logging.getLogger(__name__)` вместо `app.observability.logging.get_logger`.
- **Acceptance**: `digest.py` импортирует `get_logger` и использует его; логи в JSON-формате.

**Пункт 2 — P-09 dead code**:
- `app/search/lexical.py:136-138` — no-op `except Exception as exc: raise exc`.
- **Acceptance**: блок удалён.

**Пункт 3 — C-01 warmup bypasses singleton**:
- `app/main.py:25-30` lifespan создаёт новый `RerankerService(config=settings.reranker)` для warmup вместо вызова `get_reranker_service()`.
- **Acceptance**: warmup вызывает `get_reranker_service()` (singleton); модель загружается в том же экземпляре, который обслуживает запросы.

**Пункт 4 — C-05 missing factory**:
- `app/api/deps.py` не содержит `get_search_orchestrator()` (route строит orchestrator inline).
- **Acceptance**: `get_search_orchestrator()` добавлен в `deps.py`; route использует `Depends(get_search_orchestrator)`.

**Пункт 5 — C-00 baseline_v1.json manual pre-fill**:
- `eval/baselines/baseline_v1.json` предзаполнен `{"recall@10":0.42, "ndcg@10":0.38, "mrr":0.51}` (forbidden pattern в C-00).
- **Acceptance**: файл обнулён (пустой JSON `{}` или заглушки); первый запуск nightly eval его сгенерирует.

**Пункт 6 — P-11 504 vs 500**:
- `app/api/routes/search.py:77-81` возвращает 504 на both-channel timeout (спека говорит 500).
- **Acceptance**: либо вернуть 500 (по спеке), либо обновить спеку на 504 (приоритет — спека; но 504 семантически корректнее — задокументировать решение).

**Пункт 7 — C-07 EvalDataset table_args Django-ism**:
- `app/db/models/eval_dataset.py` использует `{"info": {"unique_together": [...]}}` (Django-ism).
- **Acceptance**: заменён на `sa.UniqueConstraint("name", "version", name="uq_eval_datasets_name_version")` или удалён (миграция уже создаёт constraint).

**Пункт 8 — P-16 alert panel type**:
- `grafana/provisioning/dashboards/mvp-dead-letters.json` Panel 4 — type `"alertlist"`, а не real Grafana alert rule.
- **Acceptance**: конвертирован в real alert rule (Grafana 9+ unified alerting) или явно отмечен как visualization-only.

**Пункт 9 — P-16 dashboard schemaVersion**:
- MVP-дашборды тоже не содержат `schemaVersion`.
- **Acceptance**: добавлен `"schemaVersion": 39` во все 4 `mvp-*.json`.

**Пункт 10 — C-10 latency benchmark**:
- `test_critical_dod_6_pushdown_latency` помечен `@pytest.mark.skip`; реального benchmark'а push-down latency на 100k docs нет.
- **Acceptance**: либо реализовать benchmark (seed 100k docs → filter `category='ML'` → p99 ≤ 50ms), либо явно задокументировать, что NFR покрывается внешним load-тестом (`scripts/run_nfr_load_critical.py`).

**Acceptance Criteria (общие)**:
- [ ] Все 10 пунктов выполнены (или явно задокументированы как wontfix с обоснованием).
- [ ] `pytest tests/ -v` (unit, без Docker) → без новых failures.
- [ ] `ruff check app/` → без ошибок.
- [ ] `mypy app/` → без новых ошибок.

**Forbidden patterns**:
- ❌ Не менять публичные API без обновления спеки.
- ❌ Не оставлять `# TODO` без issue-ссылки.

**Suggested files**:
- `app/reconciler/digest.py`
- `app/search/lexical.py`
- `app/main.py`
- `app/api/deps.py`
- `app/api/routes/search.py`
- `app/db/models/eval_dataset.py`
- `eval/baselines/baseline_v1.json`
- `grafana/provisioning/dashboards/mvp-dead-letters.json`
- `grafana/provisioning/dashboards/mvp-*.json`
- `tests/e2e/test_critical_dod.py` (DoD 6 unskip или документация)

---

## 5. Контроль качества после всех фиксов

### 5.1. Regression checklist

После выполнения B-00..B-12 запустить полный набор проверок:

```bash
# Static checks
ruff check app/
mypy app/

# Unit tests (no Docker)
pytest tests/unit/ -v

# Integration tests (Docker required)
docker compose up -d
pytest tests/integration/ -v -m slow

# E2E MVP
bash scripts/run_dod_check.sh

# E2E Critical
bash scripts/run_dod_check_critical.sh

# NFR
python scripts/run_nfr_load_critical.py
```

### 5.2. Ожидаемый результат

| Проверка | Ожидание |
|---|---|
| `ruff` | 0 errors |
| `mypy` | 0 errors |
| Unit tests | все зелёные |
| Integration tests | все зелёные (с Docker) |
| `run_dod_check.sh` | exit 0 |
| `run_dod_check_critical.sh` | exit 0 |
| NFR | latency p99 ≤ 350ms (GPU), pushdown p99 ≤ 50ms, 99% SLA |

### 5.3. Приоритезация

1. **Сначала**: B-00, B-01, B-02 (P0) — без них production невозможен.
2. **Затем**: B-03..B-06 (P1) — без них acceptance criteria конкретных промптов не выполнены.
3. **Потом**: B-07..B-09 (P2) — латентные баги и неполные DoD.
4. **В конце**: B-10..B-12 (P3) — полировка, можно в отдельном PR.

---

## Приложение A. Сводная таблица дефектов

| ID | Приоритет | Промпт | Файл | Строка | Краткое описание |
|----|---|---|---|---|---|
| B-00 | P0 | P-07 | `app/api/routes/index.py` | 35 | `get_session()` вместо `async_session_factory` |
| B-01 | P0 | C-03 | `app/reranker/circuit_breaker.py` | 82-122 | `call()` не вызывает `_check_and_transition()` |
| B-02 | P0 | C-14 | `tests/e2e/test_critical_dod.py` | 222-223, 215, 360, 454-459 | IndentationError + metric label + patch target + pydantic access |
| B-03 | P1 | C-02 | `app/search/fusion.py` | 84 | Single-element normalization → 0.0 вместо 1.0 |
| B-04 | P1 | C-07 | `app/db/queries/eval.py` | 98 | `func.max` без импорта + 3 др. дефекта |
| B-05 | P1 | C-08 | `app/api/routes/index.py` | 84-117 | Нет `Cache-Control: no-store` + rate-limit |
| B-06 | P1 | C-11/C-12 | `app/services/indexing.py` | 124-144 | `outbox_throttled_total` не инкрементируется |
| B-07 | P2 | — | `app/services/qdrant.py` | 75 | Нет `import asyncio` |
| B-08 | P2 | P-17 | `tests/e2e/` | — | DoD 3 stub, DoD 5 skip, NFR downscale, port bug |
| B-09 | P2 | C-13 | `grafana/provisioning/dashboards/critical-*.json` | — | Нет alerts, schemaVersion, `${DS_PROMETHEUS}` |
| B-10 | P3 | C-12 | `tests/unit/test_metrics_test.py` | 241-266 | Параметризация 12/22 метрик |
| B-11 | P3 | C-04 | `app/search/speculative.py` | 95, 188-191 | Нет `remaining_count`, `total_rerank_ms` всегда 0 |
| B-12 | P3 | несколько | несколько | — | 10 мелких дефектов (см. тело промпта) |
