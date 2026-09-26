# CRITICAL-PROMPTS — Промпты для ИИ-агента (Claude Code) по реализации Фазы Critical гибридного поиска

> **Источник**: `ARCHITECT.md` v2.0 (TRIZ-applied) + `ROADMAP.md` v1.0 + `MVP-PROMPTS.md` v1.0
> **Целевая фаза**: Critical (ROADMAP §4) — production-safe baseline: reranker, safety nets, оффлайн-eval
> **Стек**: Python 3.11+ (FastAPI, SQLAlchemy 2.x async), PostgreSQL 16, Qdrant 1.x, Redis 7, GPU-нода (для cross-encoder)
> **Целевой агент**: Claude Code
> **Стиль промптов**: spec-driven (Context / Goal / Constraints / References / Acceptance Criteria / Forbidden patterns / Suggested files)
> **Гранулярность**: 1 промпт = 1 задача (~30–90 минут работы агента)
> **Предусловие**: Фаза MVP завершена (см. `MVP-PROMPTS.md` P-00..P-17, все DoD §3.4 зелёные, exit criteria §9.1 выполнены)

---

## 0. Как пользоваться этим документом

### 0.1. Порядок выполнения

Промпты пронумерованы `C-00` … `C-14`. Большинство имеет **upstream-зависимости** от MVP-PROMPTS (P-01..P-17) и от других Critical-промптов. Параллельно можно запускать только промпты из независимых подсистем.

Рекомендуемый порядок:

1. **C-00** (Phase bootstrap) — обязательно первым: добавляет env-переменные, зависимости в `pyproject.toml`, baseline eval-датасет, заготовку для `wait_for_index_token`.
2. Параллельно: **C-01** (Cross-encoder service) и **C-08** (`wait_for_index` endpoint) — независимы.
3. Последовательно: **C-02** (Weighted fusion) → **C-03** (Circuit breaker) → **C-04** (Speculative rerank) — выстраивают пайплайн качества.
4. Параллельно: **C-06** (Facets), **C-09** (Degraded mode), **C-10** (Push-down filters), **C-11** (Adaptive throttle) — изолированные расширения search/index.
5. **C-05** (Search API integration) — после C-01..C-04, склеивает rerank + weighted + degraded в `POST /search`.
6. **C-07** (Offline-eval nightly job) — после C-05, нужна готовая стратегия fusion для прогона.
7. **C-12** (Observability additions) — после C-03, C-10, C-07 (метрики считаются с этих компонентов).
8. **C-13** (Grafana dashboards) — после C-12.
9. **C-14** (E2E + DoD) — последним, после готовности всех компонентов.

### 0.2. Карта промптов

| ID | Компонент ROADMAP §4.2 | Зависимости | ARCHITECT §ref | ROADMAP §ref | Ожидаемый артефакт |
|----|---|---|---|---|---|
| **C-00** | Phase bootstrap | все P-* | §17.2, §11.5 | §4 (вводная) | env-расширение, `requirements` updates, eval-dataset stub, `wait_for_index_token` поле в schema |
| **C-01** | Cross-encoder reranker (§4.2.1) | C-00 | §7.3, §5.1 | §4.2.1 | `app/reranker/service.py` (bge-reranker-v2-m3 + батчинг) |
| **C-02** | Weighted fusion (§4.2.2) | C-00 | §7.2 | §4.2.2 | `app/search/fusion.py` (`weighted_fuse`, min-max norm) |
| **C-03** | Circuit breaker для reranker (§4.2.3) | C-01 | §6.6 | §4.2.3 | `app/reranker/circuit_breaker.py` (rolling window, auto-open) |
| **C-04** | Speculative rerank (§4.2.4) | C-01, C-05 | §6.5 | §4.2.4 | `app/search/speculative.py` (параллельный rerank на lex top-10) |
| **C-05** | Search API integration | C-01..C-04 | §14.1, §6.1 | §4.2.1–4.2.4 | обновлённый `app/api/routes/search.py` + `SearchOrchestrator` |
| **C-06** | Фасеты в PostgreSQL (§4.2.5) | C-00 | §8.4 | §4.2.5 | `app/search/facets.py` + поле `facets` в `SearchResponse` |
| **C-07** | Оффлайн-eval nightly job (§4.2.6) | C-05, C-00 | §11.5 | §4.2.6 | `app/eval/nightly.py` + `eval_datasets` таблица + CI gate |
| **C-08** | `wait_for_index` endpoint (§4.2.7) | P-07 | §14.3, §4.5 | §4.2.7 | `app/api/routes/index.py::index_status` |
| **C-09** | Degraded mode (lex-only) (§4.2.8) | P-09, P-10 | §14.1, §4.6 | §4.2.8 | feature flag `vector_search_enabled`, `partial_result()` в orchestrator |
| **C-10** | Push-down фильтры basic (§4.2.9) | P-09, P-10 | §8.2 | §4.2.9 | `app/search/pushdown.py` (selectivity по `reltuples`) |
| **C-11** | Адаптивный throttle (§4.2.10) | P-07 | §4.6 | §4.2.10 | 202 Accepted при `pending_count > 50k` + watermark 100k → full reindex stub |
| **C-12** | Observability additions | C-03, C-10, C-07 | §11.1 | §4.5 | `app/observability/metrics.py` extensions |
| **C-13** | Grafana Critical dashboards | C-12 | §11.1 | §4.5 | `grafana/provisioning/dashboards/critical-*.json` |
| **C-14** | E2E + DoD Critical | все C-* | §3.4, §3.5 (аналог) | §4.4, §4.5 | `tests/e2e/test_critical_dod.py`, `scripts/run_dod_check_critical.sh` |

### 0.3. Глобальные правила для агента

1. **Перед стартом каждого промпта** агент обязан прочитать соответствующие §ref из `ARCHITECT.md` и `ROADMAP.md` (файлы лежат в `analitics/`). Если в промпте указано `ARCHITECT §6.6` — открыть `analitics/ARCHITECT.md`, найти раздел `### 6.6`, прочитать целиком.
2. **Не выходить за рамки Critical**. Любые фичи из §5–§6 ROADMAP (A/B-фреймворк, персонализация, MMR, OTel tracing, shadow traffic, chaos weekly, каскадный fusion, LambdaMART, CDC, tiering, multi-armed bandit) — **запрещены** в этой фазе. Если промпт молчит про фичу — её не реализовывать.
3. **TRIZ-gates Critical встраиваются вместе с фичей** (ROADMAP §4.3 / §10.2), не откладываются:
   - Circuit breaker для reranker — обязательно в C-03.
   - Speculative rerank — обязательно в C-04.
   - `wait_for_index_token` polling — обязательно в C-08.
   - `degraded` flag + `partial_result()` — обязательно в C-09.
   - Nightly eval как regression gate — обязательно в C-07.
   - 202 Accepted при `pending_count > threshold` — обязательно в C-11.
   - Push-down фильтры в Postgres — обязательно в C-10.
4. **Сохранять единый `QdrantPayload`** — pydantic-модель из MVP (P-04). Если для reranker нужны дополнительные поля payload — расширять модель, не плодить дубликаты.
5. **Все SQL-миграции** — через Alembic, идемпотентные, с `downgrade()`. Нумерация продолжается: `005_*`, `006_*`, …
6. **Все env-dependent параметры** (GPU device, reranker model name, circuit breaker thresholds, eval-dataset path, watermark thresholds) — через `pydantic-settings` (`app/config.py`), никаких хардкодов.
7. **GPU-зависимость** — cross-encoder работает на GPU (ARCHITECT §7.3: ~5 мс/пара на GPU, недопустимо на CPU). Для dev-окружения допустима mock-реализация, возвращающая RRF-скор (фиксируется в `app/config.py::RerankerConfig.mock_mode`).
8. **Self-check перед завершением промпта**:
   - `ruff check app/ tests/` — без ошибок.
   - `mypy app/` — без ошибок (strict-режим для нового кода).
   - `pytest tests/unit/<module>_test.py` — зелёный.
   - Если промпт добавляет endpoint: `curl`-проверка против локально поднятого `docker-compose up`.
9. **После завершения промпта** — краткий отчёт: что создано, какие §ref закрыты, какие отклонения от спеки (если есть) и почему.
10. **Не ломать MVP-контракты**: `POST /index`, `DELETE /index/{doc_id}`, `POST /search` с `fusion="rrf"` и `rerank=false` должны работать идентично MVP. Новые поля добавляются как опциональные.

### 0.4. Расширение структуры репозитория

Critical-фаза расширяет MVP-структуру (см. `MVP-PROMPTS.md` §0.4). Новые каталоги/файлы отмечены `★`.

```
.
├── ARCHITECT.md
├── ROADMAP.md
├── MVP-PROMPTS.md
├── CRITICAL-PROMPTS.md              # ★ этот документ
├── BACKLOG.md                       # ★ этот документ
├── docker-compose.yml               # + сервис reranker (или in-process)
├── pyproject.toml                   # + FlagEmbedding / sentence-transformers для reranker
├── .env.example                     # + RERANKER_* переменные
├── alembic/
│   └── versions/
│       ├── 001_documents.py
│       ├── 002_outbox.py
│       ├── 003_embedding_models.py
│       ├── 004_dead_digest.py
│       ├── 005_eval_datasets.py     # ★ C-07
│       └── 006_search_outbox_watermark.py  # ★ C-11 (если нужны доп. поля)
├── app/
│   ├── api/
│   │   ├── routes/
│   │   │   ├── index.py             # + GET /index/status/{token} (C-08)
│   │   │   ├── search.py             # обновлён в C-05
│   │   │   └── health.py
│   │   ├── schemas.py               # + degraded, facets, rerank fields (C-05, C-06)
│   │   └── deps.py                  # + RerankerService dep (C-01)
│   ├── config.py                    # + RerankerConfig, CircuitBreakerConfig, EvalConfig, PushdownConfig, ThrottleConfig
│   ├── search/
│   │   ├── fusion.py                # + weighted_fuse (C-02)
│   │   ├── orchestrator.py          # обновлён в C-05, C-09
│   │   ├── facets.py                # ★ C-06
│   │   ├── pushdown.py              # ★ C-10
│   │   └── speculative.py           # ★ C-04
│   ├── reranker/                    # ★
│   │   ├── __init__.py
│   │   ├── service.py               # C-01 (bge-reranker-v2-m3)
│   │   ├── circuit_breaker.py       # C-03
│   │   └── exceptions.py            # C-01 (RerankerUnavailable, RerankerTimeout)
│   ├── eval/                        # ★
│   │   ├── __init__.py
│   │   ├── nightly.py               # C-07
│   │   ├── metrics.py               # C-07 (recall_at_10, ndcg_at_10, mrr)
│   │   └── datasets.py              # C-07 (загрузка eval-датасета)
│   ├── services/
│   │   ├── indexing.py              # обновлён в C-11 (throttle)
│   │   ├── qdrant.py
│   │   └── throttle.py              # ★ C-11 (watermark checks)
│   ├── db/
│   │   ├── queries/
│   │   │   ├── outbox.py            # + check_status_by_doc_id (C-08)
│   │   │   ├── eval.py              # ★ C-07 (CRUD eval_datasets, eval_results)
│   │   │   └── tenants.py           # ★ C-10 (selectivity по reltuples)
│   │   └── models/
│   │       ├── eval_dataset.py      # ★ C-07
│   │       └── eval_result.py       # ★ C-07
│   └── observability/
│       └── metrics.py               # расширяем в C-12
├── eval/                            # ★
│   ├── datasets/
│   │   └── baseline_v1.jsonl        # C-00: ≥100 запросов с релевантными doc_id
│   └── baselines/
│       └── baseline_v1.json         # C-07: snapshot nDCG/Recall baseline
├── grafana/provisioning/dashboards/
│   ├── mvp-*.json                   # из MVP
│   ├── critical-reranker.json       # ★ C-13
│   ├── critical-eval-regression.json # ★ C-13
│   ├── critical-pushdown.json        # ★ C-13
│   └── critical-circuit-breaker.json # ★ C-13
├── scripts/
│   ├── init_qdrant.py
│   ├── run_dod_check.sh
│   ├── run_dod_check_critical.sh    # ★ C-14
│   └── run_nightly_eval.sh          # ★ C-07 (cron wrapper)
└── tests/
    ├── unit/
    │   ├── test_weighted_fusion_test.py         # ★ C-02
    │   ├── test_reranker_service_test.py        # ★ C-01
    │   ├── test_circuit_breaker_test.py         # ★ C-03
    │   ├── test_speculative_rerank_test.py      # ★ C-04
    │   ├── test_facets_test.py                  # ★ C-06
    │   ├── test_pushdown_test.py                # ★ C-10
    │   ├── test_throttle_test.py                # ★ C-11
    │   ├── test_wait_for_index_test.py          # ★ C-08
    │   └── test_eval_metrics_test.py            # ★ C-07
    ├── integration/
    │   ├── test_search_with_rerank_test.py      # ★ C-05
    │   ├── test_degraded_mode_test.py           # ★ C-09
    │   ├── test_nightly_eval_test.py           # ★ C-07
    │   └── test_pushdown_integration_test.py   # ★ C-10
    └── e2e/
        └── test_critical_dod.py                # ★ C-14
```

---

## C-00 — Phase bootstrap: конфигурация, зависимости, eval-датасет baseline

> **Компонент ROADMAP §4 (вводная)** | **Артефакт**: расширение `app/config.py` + `pyproject.toml` + seed `eval/datasets/baseline_v1.jsonl`

### Context

Critical-фаза добавляет GPU-зависимый cross-encoder, throttling watermarks, eval-датасет и несколько feature flags. Все эти параметры должны быть сконфигурированы до того, как начнётся реализация компонентов — иначе каждый последующий промпт будет тащить свой минимальный конфиг и они разойдутся. Eval-датасет — отдельная забота: его собирают один раз вручную (≥100 запросов с релевантными doc_id), и он становится baseline для nightly eval (C-07).

### Goal

1. Расширить `app/config.py` секциями: `RerankerConfig`, `CircuitBreakerConfig`, `EvalConfig`, `PushdownConfig`, `ThrottleConfig`, `FeatureFlags`.
2. Добавить зависимости в `pyproject.toml`: `FlagEmbedding` (или `sentence-transformers` уже на месте — убедиться), `scikit-learn` для метрик, `numpy` уже есть.
3. Создать структуру каталогов `eval/datasets/` и `eval/baselines/`.
4. Подготовить seed eval-датасет `eval/datasets/baseline_v1.jsonl` с ≥100 запросами.
5. Расширить `IndexResponse` поле `wait_for_index_token: str | None` (если ещё `None` по умолчанию) — оно будет заполняться в C-08.
6. Расширить `SearchRequest` опциональными полями `rerank: bool = False`, `fusion: Literal["rrf", "weighted"] = "rrf"` (пока без `cascade`, `learned` — это Improvements).

### Constraints

- `RerankerConfig`:
  - `model_name: str = "BAAI/bge-reranker-v2-m3"`
  - `batch_size: int = 32`
  - `max_length: int = 512`
  - `device: str = "cuda"` (auto-detect через `torch.cuda.is_available()`, fallback `"cpu"` с warning)
  - `mock_mode: bool = False` — если `True`, возвращает RRF-скор как mock (для dev без GPU)
  - `timeout_ms: int = 500`
- `CircuitBreakerConfig`:
  - `error_rate_threshold: float = 0.05` (5%)
  - `latency_p95_threshold_ms: int = 500`
  - `window_seconds: int = 60`
  - `cooldown_seconds: int = 60`
- `EvalConfig`:
  - `dataset_path: str = "eval/datasets/baseline_v1.jsonl"`
  - `baselines_dir: str = "eval/baselines/"`
  - `recall_regression_threshold: float = 0.02`
  - `ndcg_regression_threshold: float = 0.01`
  - `cron: str = "0 2 * * *"` — nightly 02:00 UTC
- `PushdownConfig`:
  - `selectivity_threshold: float = 0.1` — если оценка селективности < 0.1, включаем push-down
  - `max_candidate_ids: int = 5000`
- `ThrottleConfig`:
  - `pending_warn_threshold: int = 50000` — 202 Accepted
  - `pending_reindex_threshold: int = 100000` — триггер full reindex stub
- `FeatureFlags`:
  - `vector_search_enabled: bool = True` — для degraded mode (C-09)
  - `rerank_enabled: bool = True` — глобальный kill switch для reranker
  - `weighted_fusion_enabled: bool = True`
  - `pushdown_enabled: bool = True`
- `pyproject.toml` dependencies:
  - `FlagEmbedding>=1.2.0` (или `sentence-transformers>=2.7` если уже используется — убедиться, что он поддерживает `BAAI/bge-reranker-v2-m3` через `CrossEncoder` class).
  - `scikit-learn>=1.4` (для `ndcg_score`, `roc_auc_score` в eval).
- Eval-датасет формат (JSONL, один запрос на строку):
  ```json
  {"query_id": "q001", "query": "как настроить hybrid search", "tenant_id": "...", "relevant_doc_ids": ["uuid1", "uuid2"], "language": "ru"}
  ```
  ≥100 запросов, минимум 1 релевантный doc_id на запрос. Если данных нет — сгенерировать синтетически на основе seed-документов из MVP-тестов (10 документов × 10 запросов = 100 пар).
- Baseline snapshot `eval/baselines/baseline_v1.json`: `{"rrf": {"recall@10": 0.42, "ndcg@10": 0.38, "mrr": 0.51}, "weighted": {...}, "weighted+rerank": {...}}`. Если baseline ещё не считался — оставить пустой объект `{}` с пометкой `// to be filled by first nightly run`.

### References

- **ARCHITECT §17.2** — `config.yaml` приложения, расширить секциями для reranker/circuit-breaker/eval.
- **ARCHITECT §11.5** — nightly eval формат, метрики.
- **ARCHITECT §7.3** — параметры cross-encoder (batch, max_length, GPU).
- **ROADMAP §4 (вводная)** — цели фазы Critical.
- **ROADMAP §4.2.1** — параметры reranker.
- **ROADMAP §4.2.3** — параметры circuit breaker.
- **ROADMAP §4.2.6** — параметры eval (thresholds).
- **ROADMAP §4.2.9** — параметры push-down (`selectivity_estimate < 0.1`).
- **ROADMAP §4.2.10** — параметры throttle (50k, 100k watermarks).

### Acceptance Criteria

- `app/config.py` содержит все 6 новых секций с указанными дефолтами.
- `pyproject.toml` — `uv sync` выполняется без ошибок, новые зависимости установлены.
- `.env.example` обновлён: добавлены все новые env-переменные с примерами значений.
- `eval/datasets/baseline_v1.jsonl` существует, содержит ≥100 строк валидного JSONL.
- `eval/baselines/baseline_v1.json` существует (допустим пустой JSON-объект с комментарием).
- `app/api/schemas.py::SearchRequest` имеет поля `rerank: bool = False` и `fusion: Literal["rrf", "weighted"] = "rrf"`.
- `app/api/schemas.py::IndexResponse` имеет поле `wait_for_index_token: str | None = None`.
- `ruff check`, `mypy app/`, `pytest` — зелёные (без new failures).
- Smoke-тест `tests/unit/test_critical_config_test.py` — загружает конфиг из env и проверяет дефолты.

### Forbidden patterns

- Не хардкодить пороги в коде — только через `pydantic-settings`.
- Не добавлять фичи из §5+ (персонализация, A/B, MMR, OTel).
- Не использовать `Literal["cascade", "learned"]` в `fusion` — это Improvements.
- Не создавать `users` или `tenant_search_stats` таблицы — это Production-Ready.
- Не заполнять `baseline_v1.json` вручную — пусть первый запуск nightly eval (C-07) его сгенерит.

### Suggested files

- `app/config.py` — расширить
- `pyproject.toml` — обновить dependencies
- `.env.example` — обновить
- `app/api/schemas.py` — расширить `SearchRequest`, `IndexResponse`
- `eval/datasets/baseline_v1.jsonl` — новый seed
- `eval/baselines/baseline_v1.json` — пустой JSON-объект
- `eval/README.md` — формат датасета, как обновлять baseline
- `tests/unit/test_critical_config_test.py`

---

## C-01 — Cross-encoder reranker service: `BAAI/bge-reranker-v2-m3`

> **Компонент ROADMAP §4.2.1** | **Артефакт**: `app/reranker/service.py`

### Context

Cross-encoder — главный quality-буст Critical-фазы: nDCG@10 +5–10% над RRF. Модель `BAAI/bge-reranker-v2-m3` принимает пару `(query, doc_text)` и возвращает scalar relevance score (не embeddings). Latency критична: на GPU ~5 мс/пара, для 50 пар ~250 мс — укладывается в SLO p99 ≤ 350 мс. Архитектурно reranker — отдельный сервис в коде (`app/reranker/`), вызывается из `SearchOrchestrator` (C-05) через `Depends`.

### Goal

Реализовать класс `RerankerService` с методами:
- `async def rerank(self, query: str, docs: list[RerankCandidate], top_k: int | None = None) -> list[RerankResult]`
- `async def warmup(self) -> None` — предзагрузка модели при старте приложения.

`RerankCandidate` и `RerankResult` — pydantic-модели в `app/reranker/schemas.py`.

### Constraints

- Модель: `BAAI/bge-reranker-v2-m3` через `FlagEmbedding` (`FlagReranker` class) или `sentence-transformers` (`CrossEncoder` class). Зафиксировать в `pyproject.toml`.
- Загрузка модели — lazy при первом вызове `rerank()`, либо при `warmup()` (если FastAPI lifespan его вызывает).
- GPU приоритет: `torch.cuda.is_available()` → `device="cuda"`, иначе `device="cpu"` с warning. На CPU inference недопустим в проде (latency × 50), но допустим в dev-тестах.
- Mock mode (`RerankerConfig.mock_mode=True`): `rerank()` возвращает RRF-скор (или равномерный 1.0/rank) без вызова модели — для локальной разработки без GPU.
- Батчинг: `FlagReranker.compute_score(pairs, batch_size=32)` или эквивалент. Пары: `[(query, doc_text), ...]`. `doc_text` — первые 512 токенов `title + "\n" + content` (или `content` целиком, если короткий).
- Normalize: модель возвращает raw logits; для совместимости с fusion нужно применять sigmoid (`1 / (1 + exp(-score))`), чтобы score был в `[0, 1]`.
- Async: методы async, но inference синхронный → `asyncio.to_thread(...)`.
- Thread safety: `FlagReranker` потокобезопасен на inference, но загрузка — нет. `asyncio.Lock` на lazy-load.
- Timeout: `RerankerConfig.timeout_ms` (default 500). Если inference длится дольше — `asyncio.TimeoutError` → `RerankerTimeoutException` (обрабатывается в C-03 circuit breaker).
- Логирование: `logger.info("Rerank done", extra={"query_len": len(query), "n_docs": len(docs), "device": "cuda"/"cpu", "inference_ms": ...})`.
- Если `docs == []` — возвращать `[]` немедленно, без вызова модели.

### References

- **ARCHITECT §7.3** — cross-encoder пайплайн, latency, формула.
- **ARCHITECT §5.1** — выбор модели (bge-reranker-v2-m3 — архитектурно родствен bge-m3).
- **ARCHITECT §6.1** — место reranker в query pipeline (после fusion).
- **ROADMAP §4.2.1** — параметры: top-50 после RRF, батч пар, p99 ≤ 350 мс, `rerank=true`.
- **ROADMAP §4.6** — риск «Cross-encoder требует GPU»: mitigation — mock_mode для dev.

### Acceptance Criteria

- `RerankerService().rerank("test query", [RerankCandidate(doc_id=..., text="hello world", score=0.5)], top_k=10)` возвращает `list[RerankResult]` длиной 1.
- `RerankResult.score` ∈ `[0, 1]` (после sigmoid).
- Результаты отсортированы по `score` убыванию.
- Если `top_k=5` и `docs` имеет 10 элементов — возвращается 5 элементов (top-5 после rerank).
- При `mock_mode=True` — модель не загружается, score равномерно распределён (`1.0, 0.9, 0.8, ...`) или равен input `score` (RRF).
- Timeout: при искусственной задержке в mock (через `asyncio.sleep(0.6)`) и `timeout_ms=500` — `RerankerTimeoutException`.
- GPU-тест (маркер `@pytest.mark.gpu`): на машине с CUDA — `device="cuda"`, inference 50 пар < 500 мс.
- CPU-тест: на машине без CUDA — `device="cpu"`, mock_mode=True, тест проходит.
- Лог содержит `device`, `inference_ms`, `n_docs`.
- `RerankerService` внедряется через FastAPI `Depends(get_reranker_service)` (singleton через `lru_cache`).

### Forbidden patterns

- Не использовать `transformers` напрямую — только `FlagEmbedding` или `sentence-transformers.CrossEncoder`.
- Не загружать модель при импорте модуля — только lazy или `warmup()`.
- Не вызывать синхронный inference в event loop — только `await asyncio.to_thread(...)`.
- Не возвращать raw logits — обязательно sigmoid (иначе score может быть отрицательным, ломает fusion).
- Не использовать `RerankerService` как глобальный синглтон в коде — только через FastAPI Depends.
- Не забывать `RerankerConfig.mock_mode` — без него dev без GPU не работает.

### Suggested files

- `app/reranker/__init__.py`
- `app/reranker/service.py` (`class RerankerService`)
- `app/reranker/schemas.py` (`RerankCandidate`, `RerankResult`)
- `app/reranker/exceptions.py` (`RerankerUnavailableException`, `RerankerTimeoutException`)
- `app/api/deps.py` — добавить `get_reranker_service()` factory
- `app/main.py` — `warmup()` вызов в FastAPI lifespan (если `RERANKER_WARMUP=true`)
- `tests/unit/test_reranker_service_test.py` (с mock_mode=True)
- `tests/conftest.py` — фикстура `reranker_service_mock`

---

## C-02 — Weighted fusion (min-max нормализация, α-коэффициент)

> **Компонент ROADMAP §4.2.2** | **Артефакт**: расширение `app/search/fusion.py`

### Context

RRF (MVP) работает с рангами и не чувствителен к масштабу скоров. Weighted fusion — альтернатива, требующая калибровки: `weighted_score = α * norm(BM25) + (1-α) * norm(cosine)`. Даёт +2–5% над RRF при правильном `α`, но чувствителен к выбросам (мин-макс нормализация растягивает outliers). Активируется через `fusion='weighted'` в запросе.

### Goal

Реализовать `def weighted_fuse(lex: list[tuple[UUID, float]], vec: list[tuple[UUID, float]], alpha: float = 0.5) -> list[tuple[UUID, float]]` в `app/search/fusion.py` (рядом с существующей `rrf_fuse` из MVP).

### Constraints

- Формула (из ARCHITECT §7.2):
  ```python
  def weighted_fuse(lex, vec, alpha=0.5):
      def norm(items):
          if not items:
              return {}
          scores = [s for _, s in items]
          lo, hi = min(scores), max(scores)
          rng = hi - lo or 1.0  # защита от деления на 0
          return {d: (s - lo) / rng for d, s in items}
      n_lex = norm(lex)
      n_vec = norm(vec)
      all_ids = set(n_lex) | set(n_vec)
      fused = []
      for d in all_ids:
          s = alpha * n_lex.get(d, 0.0) + (1 - alpha) * n_vec.get(d, 0.0)
          fused.append((d, s))
      return sorted(fused, key=lambda x: -x[1])
  ```
- `alpha` — параметр запроса (в `SearchRequest.fusion_alpha: float = Field(0.5, ge=0.0, le=1.0)`), но на Critical фиксируется `0.5` default; тюнится оффлайн в C-07.
- Если один из каналов пуст (нет лексических попаданий) — `weighted_fuse` возвращает только векторный топ с `norm(vec)` за `alpha=1.0` (потому что lex-норма пуста).
- Если оба пусты — `[]`.
- Симметричность: `weighted_fuse(lex, vec, alpha)` и `weighted_fuse(vec, lex, 1-alpha)` дают тот же порядок (проверяется тестом).
- Нормализация по выборке результатов, не по глобальной статистике (мин/макс среди top-K кандидатов).
- Дедупликация по `doc_id`: если документ есть в обоих каналах — сумма его нормализованных скоров; если только в одном — `0` для другого канала.

### References

- **ARCHITECT §7.2** — формула weighted fusion, pseudocode, свойства, `α ∈ [0, 1]`.
- **ARCHITECT §7.6** — сравнительная таблица: weighted даёт +2–5% над RRF.
- **ROADMAP §4.2.2** — `α = 0.5` default, тюнинг оффлайн (C-07).
- **ROADMAP §7.7 (ТРИЗ)** — взвешенное среднее,自适应 α — Improvements (multi-armed bandit).

### Acceptance Criteria

- `weighted_fuse([(id1, 0.8), (id2, 0.6)], [(id2, 0.9), (id3, 0.1)], alpha=0.5)` возвращает сортированный список `[(id2, ...), (id1, ...), (id3, ...)]` (или эквивалент).
- `weighted_fuse([], [(id1, 0.9)])` → `[(id1, 1.0)]` (нормализация одного элемента даёт 1.0).
- `weighted_fuse([(id1, 0.5)], [])` → `[(id1, 1.0)]`.
- `weighted_fuse([], [])` → `[]`.
- Документ в обоих каналах: `score ≈ alpha * 1.0 + (1-alpha) * 1.0 = 1.0` если он топ в обоих.
- Симметричность: `weighted_fuse(lex, vec, 0.3)` ≈ `weighted_fuse(vec, lex, 0.7)` (порядок doc_id одинаковый).
- Защита от деления на 0: все одинаковые скоры → `rng = 1.0`, нормализованный score = 0 для всех (или 0.5 — на выбор, но зафиксировать).
- Тест на выбросы: если lex скоры `[0.1, 0.2, 0.3, 0.4, 100.0]` — после нормализации большинство будет в `[0, 0.003]`, что демонстрирует чувствительность weighted к выбросам (зафиксировать как known limitation в тесте).

### Forbidden patterns

- Не использовать z-score нормализацию — только min-max (по спеке ARCHITECT §7.2).
- Не использовать глобальные min/max (по всей коллекции) — только по выборке top-K кандидатов.
- Не хардкодить `alpha=0.5` в функции — принимать параметром.
- Не использовать weighted fusion как default — RRF остаётся default на Critical.
- Не забывать защиту от деления на 0 (`rng = hi - lo or 1.0`).
- Не использовать weighted fusion в `fusion='cascade'` — это Improvements.

### Suggested files

- `app/search/fusion.py` — добавить `weighted_fuse` (если уже есть заглушка из MVP P-11 — заменить)
- `app/api/schemas.py` — расширить `SearchRequest` полем `fusion_alpha: float = Field(0.5, ge=0.0, le=1.0)`
- `tests/unit/test_weighted_fusion_test.py` — покрывает все кейсы выше

---

## C-03 — Circuit breaker для reranker

> **Компонент ROADMAP §4.2.3, §4.3** | **Артефакт**: `app/reranker/circuit_breaker.py`

### Context

Cross-encoder — тяжёлая GPU-модель. Под нагрузкой или при сбое GPU latency растёт → каскадный таймаут всего поиска. Circuit breaker (TRIZ-gate §9 + §19) заранее подложен: при `error_rate > 5%` или `latency_p95 > 500 мс` за минуту — auto-disable rerank на 60 сек. На это время API отдаёт fusion-only результаты с `degraded: true` (сам `degraded` flag заполняется в C-09, здесь — только сам breaker).

### Goal

Реализовать класс `RerankerCircuitBreaker` с методами:
- `async def call(self, fn, *args, **kwargs) -> Any` — обёртка вокруг `RerankerService.rerank`.
- `def is_open(self) -> bool` — текущее состояние.
- `def state(self) -> Literal["closed", "open", "half_open"]` — для метрик.

Состояния: `closed` (норма), `open` (rerank отключён), `half_open` (пробуем 1 запрос после cooldown).

### Constraints

- Параметры (из C-00 `CircuitBreakerConfig`):
  - `error_rate_threshold: float = 0.05` (5%)
  - `latency_p95_threshold_ms: int = 500`
  - `window_seconds: int = 60` — rolling window
  - `cooldown_seconds: int = 60` — сколько держать открытым
- Rolling window: `collections.deque` с `(timestamp, latency_ms, success: bool)`. Старые записи (старше `window_seconds`) выкидываются.
- Открытие: если за window `errors / total > error_rate_threshold` ИЛИ `percentile(latencies, 95) > latency_p95_threshold_ms` → переход в `open`.
- В состоянии `open`: `call()` не вызывает `fn`, сразу возвращает `None` (или специальный `CircuitOpen` sentinel). В логе: `logger.warning("Circuit breaker open", extra={"reason": ..., "until": ...})`.
- Переход `open → half_open`: после `cooldown_seconds` с момента открытия. В `half_open` пропускается 1 запрос (probe); при успехе → `closed`, при ошибке → снова `open` (cooldown сбрасывается).
- Метрики: gauge `circuit_breaker_state` (0=closed, 1=half_open, 2=open), counter `circuit_breaker_opened_total`, counter `circuit_breaker_requests_total{result="success|rejected|error"}`.
- Thread safety: `asyncio.Lock` на обновление state, но не на сам `fn` (иначе убьём параллелизм).
- Lazy singleton через FastAPI Depends.
- При открытом breaker — `RerankerService` не вызывается вообще (early return в `SearchOrchestrator`).

### References

- **ARCHITECT §6.6** — спецификация circuit breaker (latency > 500 мс p95, error > 5%, отключение 60 сек).
- **ARCHITECT §6.7 (ТРИЗ)** — Принцип 9 (Предварительное антидействие): заранее подложенный механизм отключения; Принцип 19 (Периодическое действие): окно 60 сек, переоткрытие.
- **ROADMAP §4.2.3** — параметры (5%, 500 мс, 60 сек).
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §10.2** — TRIZ-gate Critical (таблица).
- **ROADMAP §4.6** — риск «Reranker деградирует под нагрузкой»: mitigation — circuit breaker.

### Acceptance Criteria

- `RerankerCircuitBreaker.call(reranker.rerank, query, docs)` в норме (`closed`) — вызывает `fn`, возвращает результат.
- После 5 ошибок из 100 вызовов (`error_rate = 5%`) — `is_open() == True`, последующие `call()` не вызывают `fn`.
- После 60 сек cooldown — `state() == "half_open"`, следующий `call()` вызывает `fn` (probe).
- В `half_open` при успехе probe → `state() == "closed"`, нормальный режим.
- В `half_open` при ошибке probe → `state() == "open"`, cooldown сбрасывается (ещё 60 сек).
- При latency p95 > 500 мс за минуту (искусственно замедлить `fn` через mock) — `is_open() == True`.
- Метрики `circuit_breaker_state`, `circuit_breaker_opened_total`, `circuit_breaker_requests_total{result=...}` публикуются в `/metrics`.
- `RerankerCircuitBreaker` внедряется через FastAPI Depends, singleton.
- Тест: 200 параллельных вызовов, 11 ошибок (5.5%) → `is_open() == True` после первой ошибки после порога.

### Forbidden patterns

- Не использовать `circuitbreaker` библиотеку — реализовать вручную (тривиально, и логика rolling window / half-open в разных библиотеках разная).
- Не блокировать event loop при обновлении state — `asyncio.Lock`, не `threading.Lock`.
- Не забывать `half_open` состояние — без него breaker либо никогда не откроется, либо будет мерцать.
- Не использовать synchronous timer для cooldown — ленивая проверка `now() - opened_at > cooldown` при следующем `call()`.
- Не инкрементировать `circuit_breaker_opened_total` при каждом `call()` в `open` — только при переходе `closed → open`.

### Suggested files

- `app/reranker/circuit_breaker.py` (`class RerankerCircuitBreaker`)
- `app/reranker/__init__.py` — реэкспорт
- `app/api/deps.py` — `get_circuit_breaker()` factory
- `app/observability/metrics.py` — добавить 3 метрики (если их ещё нет)
- `tests/unit/test_circuit_breaker_test.py` — покрывает все переходы состояний

---

## C-04 — Speculative rerank (параллельный запуск с лексическим каналом)

> **Компонент ROADMAP §4.2.4, §4.3** | **Артефакт**: `app/search/speculative.py`

### Context

Без speculative rerank пайплайн последовательный: `lex + vec → fusion → rerank` = ~50 мс + ~150 мс + ~250 мс = 450 мс. Speculative (TRIZ-gate §21 «Проскочить») запускает cross-encoder **параллельно** с векторным поиском, используя `lex top-10` как спекулятивный вход. К моменту готовности fusion у нас уже есть rerank-скоры для 10 документов; дозапускаем rerank только для новых документов из векторного топа. Экономит ~80 мс на p99.

### Goal

Реализовать `class SpeculativeReranker` с методом:
- `async def run(self, query: str, lex_task: asyncio.Task[list[LexicalHit]], vec_task: asyncio.Task[list[VectorHit]], rerank_top_n: int = 10) -> tuple[list[VectorHit], list[RerankResult]]`

Возвращает fusion-вход (vec) + pre-computed rerank-результаты для части документов.

### Constraints

- Логика (из ARCHITECT §6.5):
  1. Запускаем `lex_task` и `vec_task` параллельно (через `asyncio.gather`).
  2. Как только `lex_task` завершён (обычно раньше, ~50 мс vs ~150 мс) — берём top-10 lex-хитов, запускаем `RerankerService.rerank(query, lex_top_10)` параллельно с продолжающимся `vec_task`.
  3. Когда `vec_task` завершён и fusion готов — берём top-50 fusion, вычитаем уже за-rerank-енные doc_ids, дозапускаем rerank только для разности.
  4. Объединяем rerank-скоры, возвращаем.
- `rerank_top_n = 10` (default, configurable в `RerankerConfig.speculative_top_n`).
- Если `lex_task` длится дольше `vec_task` (аномалия) — пропускаем speculative, ждём fusion, запускаем rerank на полном top-50 (fallback).
- Если circuit breaker открыт (C-03) — `SpeculativeReranker.run` возвращает `(vec_results, [])` без вызова reranker.
- Если `vec_task` падает с `QdrantUnavailableException` — `SpeculativeReranker.run` не падает, возвращает `([], [])` (orchestrator в C-09 переключится на lex-only).
- Timeout: общее время speculative pipeline не должно превышать `SearchRequest.timeout_ms - 50` (запас на response marshalling).
- Логирование: `logger.info("Speculative rerank", extra={"speculative_count": 10, "remaining_count": N, "total_rerank_ms": ...})`.

### References

- **ARCHITECT §6.5** — speculative rerank, экономия ~80 мс.
- **ARCHITECT §6.7 (ТРИЗ)** — Принцип 21 (Проскожить): начинаем rerank до завершения fusion.
- **ROADMAP §4.2.4** — parallel запуск, lex top-10 как speculative input.
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §4.6** — риск «Reranker деградирует»: mitigation — circuit breaker + speculative rerank.

### Acceptance Criteria

- `SpeculativeReranker.run(query, lex_task, vec_task)` возвращает кортеж `(vec_results, rerank_results)`.
- `rerank_results` содержит скоры для top-10 lex-хитов + для уникальных doc_ids из vec-топа.
- Если `lex_task` уже завершён к моменту вызова `run()` — rerank стартует немедленно.
- Если `vec_task` завершён раньше `lex_task` (искусственно замедлить lex через `pg_sleep`) — fallback: rerank на полном fusion top-50.
- Если circuit breaker открыт — `rerank_results == []`, `vec_results` всё равно возвращается.
- Latency: при mock-реализациях `lex_task=50мс`, `vec_task=150мс`, `rerank=250мс` — общее время `SpeculativeReranker.run` ≈ 200 мс (vs 450 мс без speculative).
- Тест: 100 параллельных вызовов — нет race conditions, doc_ids в rerank_results уникальны.
- Лог содержит `speculative_count`, `remaining_count`, `total_rerank_ms`.

### Forbidden patterns

- Не использовать `asyncio.wait` с `FIRST_COMPLETED` без сохранения оставшихся task'ов — они должны быть отменены или дождаться завершения.
- Не запускать rerank до завершения `lex_task` — нужны конкретные doc_ids и texts.
- Не забывать circuit breaker check — иначе при открытом breaker всё равно позвали reranker.
- Не блокировать event loop на `RerankerService.rerank` — он сам async, но его внутренний `asyncio.to_thread` нужно дождаться.
- Не вызывать `SpeculativeReranker.run` напрямую из роута — только из `SearchOrchestrator` (C-05).

### Suggested files

- `app/search/speculative.py` (`class SpeculativeReranker`)
- `app/search/__init__.py` — реэкспорт
- `app/api/deps.py` — `get_speculative_reranker()` factory
- `app/config.py` — расширить `RerankerConfig` полем `speculative_top_n: int = 10`
- `tests/unit/test_speculative_rerank_test.py` (с mock-задачами)

---

## C-05 — Search API integration: rerank + weighted + degraded flag

> **Компонент ROADMAP §4.2.1–4.2.4** | **Артефакт**: обновление `app/api/routes/search.py` + `app/search/orchestrator.py`

### Context

C-01..C-04 собрали компоненты качества; теперь их нужно интегрировать в `POST /search`. Обновлённый orchestrator: параллельно lex+vec, fusion (RRF или weighted), опционально speculative rerank (через C-04), опционально plain rerank (если speculative выключен), `degraded` flag в ответе при lex-only fallback (если C-09 ещё не подключил, здесь — заглушка).

### Goal

Обновить `SearchOrchestrator` и роут `POST /search` для поддержки:
- `fusion: Literal["rrf", "weighted"]` — выбор алгоритма fusion.
- `rerank: bool = False` — включение cross-encoder rerank (через circuit breaker).
- `fusion_alpha: float = 0.5` — параметр для weighted.
- `degraded: bool` — заполнение в response.
- `explain: bool` — расширение debug-информации (`fusion_scores`, `rerank_scores`).

### Constraints

- Обновлённый пайплайн в `SearchOrchestrator.search()`:
  1. `deadline = time.monotonic() + req.timeout_ms / 1000`
  2. Старт `lex_task`, `vec_task` через `asyncio.gather` (или `asyncio.create_task` для speculative).
  3. Если `req.rerank` и circuit breaker закрыт и `RerankerConfig.speculative_enabled` — `SpeculativeReranker.run(...)` (параллельный rerank).
  4. Если `req.rerank` и circuit breaker закрыт и speculative выключен — дождаться fusion, потом `RerankerService.rerank(query, fusion_top_50, top_k=req.top_k)`.
  5. Если circuit breaker открыт — пропустить rerank, проставить `degraded=True` (или специальный флаг `rerank_degraded=True` в debug, а `degraded` оставить для lex-only fallback C-09).
  6. Fusion: `rrf_fuse` или `weighted_fuse` в зависимости от `req.fusion`.
  7. Top-K после rerank (если был) или после fusion.
  8. Заполнить `SearchResponse.degraded` если lex-only fallback (из C-09) или `rerank_degraded` (circuit breaker).
  9. `latency_ms = int((time.monotonic() - start) * 1000)`.
- `SearchRequest` расширить (часть уже в C-00):
  - `rerank: bool = False`
  - `fusion: Literal["rrf", "weighted"] = "rrf"`
  - `fusion_alpha: float = Field(0.5, ge=0.0, le=1.0)`
- `SearchResponse` расширить:
  - `degraded: bool = False` (уже в MVP, но теперь реально заполняется)
  - `debug.fusion_strategy: str` (при `explain=True`)
  - `debug.rerank_applied: bool` (при `explain=True`)
  - `debug.rerank_degraded: bool` (при `explain=True`, если circuit breaker был открыт)
- HTTP status: всегда `200` (даже при degraded).
- Если `fusion="weighted"` и `fusion_alpha` не передан — default `0.5`.
- Если circuit breaker открыт и `rerank=True` — в response `rerank_degraded=True`, но `degraded=False` (поэтому семантически `degraded` = «упал векторный канал», `rerank_degraded` = «отключён reranker»).

### References

- **ARCHITECT §14.1** — точный API-контракт `POST /search`.
- **ARCHITECT §6.1** — pipeline обработки запроса.
- **ARCHITECT §7.1, §7.2, §7.3** — RRF, weighted, cross-encoder.
- **ARCHITECT §6.6** — circuit breaker интеграция.
- **ROADMAP §4.2.1–4.2.4** — компоненты Critical фазы.
- **ROADMAP §4.4 (DoD)** — критерии приёмки.

### Acceptance Criteria

- `POST /search` с `fusion="rrf"` и `rerank=false` — работает идентично MVP (regression test).
- `POST /search` с `fusion="weighted"` — применяет `weighted_fuse`, `debug.fusion_strategy="weighted"` (при `explain=True`).
- `POST /search` с `rerank=true` — применяет cross-encoder, `debug.rerank_applied=True`.
- `POST /search` с `rerank=true` и circuit breaker открытым — `debug.rerank_degraded=True`, ответ без rerank.
- `POST /search` с `fusion="weighted", rerank=true, fusion_alpha=0.7` — комбинированный режим.
- Latency: с `rerank=true` на GPU — p99 ≤ 350 мс (проверить через `scripts/run_dod_check_critical.sh` в C-14).
- `explain=True` возвращает debug с `fusion_strategy`, `rerank_applied`, `rerank_degraded`, `rrf_score`/`weighted_score`, `rerank_score`.
- `degraded=True` в response — только если lex-only fallback (C-09), не от circuit breaker.
- Интеграционный тест: 10 запросов подряд с `rerank=true` и mock-режимом reranker — стабильно работает.
- Интеграционный тест: открыть circuit breaker (через mock-ошибки) → следующий запрос с `rerank=true` — `debug.rerank_degraded=True`.

### Forbidden patterns

- Не запускать rerank последовательно после fusion, если speculative включён — это убьёт advantage C-04.
- Не возвращать `500` при открытом circuit breaker — это нормальная degraded-ситуация, ответ `200` с флагом.
- Не использовать `time.time()` — только `time.monotonic()`.
- Не падать, если `RerankerService` не внедрён (нет GPU) — fallback на fusion-only (но логировать warning).
- Не использовать `fusion="cascade"` или `fusion="learned"` — Improvements.
- Не добавлять `personalize`, `diversify`, `experiment_id` — это Production-Ready.

### Suggested files

- `app/api/routes/search.py` — обновить роут
- `app/search/orchestrator.py` — обновить `SearchOrchestrator`
- `app/api/schemas.py` — расширить `SearchRequest`, `SearchResponse`, `SearchHit.debug`
- `app/api/deps.py` — `get_search_orchestrator()` с зависимостями от `RerankerService`, `CircuitBreaker`, `SpeculativeReranker`
- `tests/integration/test_search_with_rerank_test.py` — все кейсы выше

---

## C-06 — Фасеты в PostgreSQL (top-N aggregation)

> **Компонент ROADMAP §4.2.5** | **Артефакт**: `app/search/facets.py` + `SearchResponse.facets`

### Context

Фасеты (категории, теги, диапазоны цен) считаются на top-N fusion-результатах, не по всей коллекции — это и быстрее, и даёт пользовательски осмысленные числа (релевантные категории для данного запроса, а не «сколько всего»). На MVP поле `facets` в `SearchResponse` было пустым `dict` (заглушка P-11); здесь наполняется.

### Goal

Реализовать `async def compute_facets(doc_ids: list[UUID], facet_fields: list[str] = None, top_n: int = 20) -> dict[str, list[FacetBucket]]` в `app/search/facets.py`. Интегрировать в `SearchOrchestrator.search()` после fusion/rerank — заполнять `SearchResponse.facets`.

### Constraints

- Default `facet_fields = ["tags", "attributes.category"]` (из ARCHITECT §8.4).
- SQL (по спеке ARCHITECT §8.4):
  ```sql
  -- для tags (array):
  SELECT tag AS value, count(*) AS cnt
  FROM documents, unnest(tags) AS tag
  WHERE id = ANY($1) AND deleted_at IS NULL
  GROUP BY tag
  ORDER BY cnt DESC
  LIMIT $2;

  -- для attributes.category:
  SELECT attributes->>'category' AS value, count(*) AS cnt
  FROM documents
  WHERE id = ANY($1) AND deleted_at IS NULL
  GROUP BY attributes->>'category'
  ORDER BY cnt DESC
  LIMIT $2;
  ```
- Для `attributes.price` (range) — отдельная логика: не фасеты, а min/max в response (опционально, можно опустить на Critical).
- `top_n` — configurable через `SearchRequest.facet_top_n: int = Field(20, ge=1, le=100)`.
- Если `doc_ids` пуст — возвращать `{}`.
- Кэширование: на Critical не делаем (это Production-Ready §5.2.11 инкрементальные фасеты).
- Tenant isolation: запросы автоматически ограничены `doc_ids` (которые уже tenant-scoped из fusion).
- В `SearchRequest` добавить поле `facets: list[str] | None = None` — список полей, по которым нужны фасеты. Если `None` — нет фасетов (экономия SQL). Если `["tags", "attributes.category"]` — обе.
- В `SearchResponse.facets: dict[str, list[FacetBucket]]` — уже есть в schema из MVP, наполняем здесь.
- Timeout: `SET LOCAL statement_timeout = '50ms'` для фасетных запросов (чтобы не тормозить основной ответ).

### References

- **ARCHITECT §8.4** — спецификация фасетов, SQL, top-N ограничение.
- **ARCHITECT §14.1** — `FacetBucket` модель, `SearchResponse.facets` поле.
- **ROADMAP §4.2.5** — фасеты считаются на top-N fusion-score, SQL пример.
- **ROADMAP §5.2.11** — инкрементальные фасеты (Production-Ready, НЕ делать здесь).

### Acceptance Criteria

- `POST /search` с `facets=["tags", "attributes.category"]` — в ответе `facets = {"tags": [{value: "news", count: 5}, ...], "attributes.category": [...]}`.
- `POST /search` без `facets` — `facets = {}` (SQL не выполняется).
- `POST /search` с `facets=["tags"]` — только `tags` в ответе, без `attributes.category`.
- Top-N: если в top-50 fusion 7 разных тегов, а `facet_top_n=20` — возвращается 7, а не 20.
- Tenant isolation: `tenant_id=A` не видит теги из `tenant_id=B` (через doc_ids).
- Пустой top-K (нет результатов): `facets = {}`.
- Timeout: при искусственной задержке PG через `pg_sleep(0.1)` — `compute_facets` таймаутится, не падает, возвращает `facets = {}` (или partial — на усмотрение, но зафиксировать в тесте).
- Latency overhead: при 50 doc_ids и 2 facet_fields — добавка ≤ 10 мс (проверить в NFR C-14).

### Forbidden patterns

- Не считать фасеты по всей коллекции (`SELECT count(*) FROM documents GROUP BY ...`) — только по top-N.
- Не использовать `LIKE` для category — только `attributes->>'category' = ...`.
- Не возвращать фасеты, если `facets` поле в запросе `None` — это пустая трата ресурсов.
- Не кэшировать в Redis — инкрементальные фасеты в Production-Ready, не раньше.
- Не делать `SELECT *` — только `tag` / `attributes->>'category'` + `count(*)`.
- Не использовать `tags && $1` (array overlap) — это для фильтров, не для фасетов. Для фасетов — `unnest(tags)`.

### Suggested files

- `app/search/facets.py` (`async def compute_facets`, `class FacetBucket` если ещё нет)
- `app/api/schemas.py` — расширить `SearchRequest` полем `facets: list[str] | None = None`, `facet_top_n: int = Field(20, ge=1, le=100)`
- `app/search/orchestrator.py` — вызывать `compute_facets` после fusion/rerank
- `app/db/queries/documents.py` — `async def fetch_facets(doc_ids, field, top_n) -> list[FacetBucket]`
- `tests/unit/test_facets_test.py`
- `tests/integration/test_search_with_facets_test.py`

---

## C-07 — Оффлайн-eval (nightly job + eval-датасет + regression gate)

> **Компонент ROADMAP §4.2.6, §4.3** | **Артефакт**: `app/eval/nightly.py` + миграция `005_eval_datasets` + CI gate

### Context

Каждое изменение в fusion-стратегиях, reranker, embedding-модели потенциально может ухудшить качество. Nightly eval (TRIZ-gate §23 «Обратная связь») — замкнутый контур: прогон eval-датасета → сравнение с baseline → алерт/блокировка релиза при регрессии. Без этого команда не сможет безопасно выкатывать изменения.

### Goal

1. Создать миграцию `005_eval_datasets` с таблицами `eval_datasets` и `eval_results`.
2. Реализовать `class NightlyEvalJob` с методом `async def run(self) -> EvalReport`.
3. CLI entry point `python -m app.eval.nightly` + cron wrapper `scripts/run_nightly_eval.sh`.
4. CI gate: при regression > threshold → exit code 1.

### Constraints

- Миграция `005_eval_datasets`:
  ```sql
  CREATE TABLE eval_datasets (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name TEXT NOT NULL UNIQUE,
      version TEXT NOT NULL,
      path TEXT NOT NULL,
      query_count INT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (name, version)
  );

  CREATE TABLE eval_results (
      id BIGSERIAL PRIMARY KEY,
      dataset_id UUID NOT NULL REFERENCES eval_datasets(id),
      strategy TEXT NOT NULL,  -- 'rrf', 'weighted', 'weighted+rerank'
      recall_at_10 FLOAT NOT NULL,
      ndcg_at_10 FLOAT NOT NULL,
      mrr FLOAT NOT NULL,
      run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      git_sha TEXT,
      extra JSONB NOT NULL DEFAULT '{}'  -- per-query details, alpha, etc.
  );
  CREATE INDEX eval_results_dataset_strategy_idx ON eval_results (dataset_id, strategy, run_at DESC);
  ```
- `NightlyEvalJob.run()`:
  1. Загрузить eval-датасет из `EvalConfig.dataset_path` (см. C-00).
  2. Зарегистрировать его в `eval_datasets` (если новый `name+version`).
  3. Для каждой стратегии в `["rrf", "weighted", "weighted+rerank"]`:
     - Для каждого запроса: `POST /search` (internal call, без HTTP overhead — через `SearchOrchestrator.search()`) с данной стратегией.
     - Сравнить top-10 с `relevant_doc_ids` из датасета.
     - Посчитать Recall@10, nDCG@10, MRR.
  4. Сохранить результат в `eval_results`.
  5. Сравнить с baseline (последняя запись `eval_results` для того же `dataset_id+strategy`).
  6. Если `recall_at_10` упал > `EvalConfig.recall_regression_threshold` (2%) OR `ndcg_at_10` упал > `EvalConfig.ndcg_regression_threshold` (1%) — отметить regression.
  7. Если regression и `EvalConfig.block_release=True` — exit code 1.
- Метрики качества:
  - `Recall@10 = |retrieved ∩ relevant| / |relevant|`
  - `nDCG@10`: стандартная формула с DCG = Σ rel_i / log2(i+1), IDCG = идеальный порядок.
  - `MRR = 1 / rank первого релевантного` (0 если нет релевантных в top-10).
- `EvalReport`: pydantic-модель со стратегиями, метриками, baseline-сравнением, флагом `regression: bool`.
- CI gate: `scripts/run_nightly_eval.sh` — запускает job, exit 1 если regression.
- Cron: `EvalConfig.cron = "0 2 * * *"` — nightly 02:00 UTC (не реализовывать cron-планировщик, только документировать что нужно настроить в k8s CronJob или systemd timer).
- Datasets: один раз зарегистрировать `baseline_v1` (из C-00) в БД при первом запуске.
- Не использовать production traffic — только eval-датасет.

### References

- **ARCHITECT §11.5** — nightly eval спецификация, метрики, baseline comparison.
- **ARCHITECT §11.8 (ТРИЗ)** — Принцип 23 (Обратная связь): regression → блокировка релиза → разработчик реагирует.
- **ARCHITECT §19.1** — топ-5 impactful TRIZ-применений: nightly eval в списке.
- **ROADMAP §4.2.6** — nightly job: eval-датасет, fusion-стратегии, Recall@10/nDCG@10/MRR, threshold 2%/1%.
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §4.4 (DoD)** — «Nightly eval ловит регрессии и блокирует релиз».
- **ROADMAP §4.6** — риск «Регрессия качества»: mitigation — nightly eval.

### Acceptance Criteria

- `alembic upgrade head` создаёт `eval_datasets`, `eval_results` с индексами.
- `python -m app.eval.nightly` запускается, логирует «Nightly eval started», прогоняет 3 стратегии, логирует «Nightly eval done» с метриками.
- `eval_results` содержит 3 новые строки (по одной на стратегию) после первого запуска.
- Повторный запуск — 3 новые строки, сравнение с предыдущими.
- Regression detection: при искусственной деградации fusion (через mock, возвращающий случайные doc_ids) — `EvalReport.regression == True`.
- CI gate: `bash scripts/run_nightly_eval.sh` — exit code 1 при regression, 0 иначе.
- Метрики в `/metrics`: gauge `eval_recall_at_10{strategy="..."}`, `eval_ndcg_at_10{strategy="..."}`, `eval_mrr{strategy="..."}`.
- Лог содержит per-query топ-3 «худших» запросов (где recall=0) для отладки.
- Тест с mock `SearchOrchestrator` — `NightlyEvalJob.run()` отрабатывает за < 60 сек (100 запросов × 3 стратегии).

### Forbidden patterns

- Не использовать production `POST /search` endpoint — internal call через `SearchOrchestrator` (без HTTP overhead).
- Не хардкодить threshold в коде — через `EvalConfig`.
- Не запускать eval при отсутствии baseline — первый запуск создаёт baseline (не падает).
- Не удалять старые `eval_results` — они нужны для trend-анализа (Production-Ready: архивация в S3).
- Не использовать `sklearn.ndcg_score` напрямую — он expects `y_true` как binary relevance, но у нас могут быть graded relevance. Реализовать свой `ndcg_at_10`.
- Не блокировать релиз, если baseline ещё не создан (первый запуск).
- Не использовать random strategy — только те 3, что в спеке.

### Suggested files

- `alembic/versions/005_eval_datasets.py`
- `app/db/models/eval_dataset.py`, `app/db/models/eval_result.py`
- `app/db/queries/eval.py` (`register_dataset`, `save_result`, `get_baseline`)
- `app/eval/__init__.py`
- `app/eval/nightly.py` (`class NightlyEvalJob`, `__main__`)
- `app/eval/metrics.py` (`recall_at_10`, `ndcg_at_10`, `mrr`)
- `app/eval/datasets.py` (`load_dataset`, `EvalQuery`)
- `scripts/run_nightly_eval.sh`
- `tests/unit/test_eval_metrics_test.py` — точные значения recall/nDCG/MRR на синтетических данных
- `tests/integration/test_nightly_eval_test.py` — end-to-end с mock orchestrator

---

## C-08 — `wait_for_index` endpoint: `GET /index/status/{token}`

> **Компонент ROADMAP §4.2.7, §4.3** | **Артефакт**: расширение `app/api/routes/index.py`

### Context

Read-your-writes SLO 5 секунд (ROADMAP §4.5) — клиент, отправивший `POST /index`, должен в течение 5 секунд увидеть документ в выдаче `POST /search`. На MVP `wait_for_index_token` возвращался как `str(doc_id)`, но сам endpoint отсутствовал. Critical добавляет polling: клиент делает `GET /index/status/{token}` раз в 200–500 мс, пока не получит `status="done"` или таймаут 5 сек.

### Goal

Реализовать роут `GET /index/status/{token}` (ARCHITECT §14.3), который по `token` (= `doc_id`) проверяет статус outbox.

### Constraints

- Логика:
  1. `token` — это UUID документа (строка). Валидация UUID.
  2. `SELECT status, updated_at FROM search_outbox WHERE document_id = $1 ORDER BY updated_at DESC LIMIT 1`.
  3. Если `status == 'done'` — response `{"token": ..., "status": "done"}`.
  4. Если `status == 'pending'` или `'in_progress'` или `'failed'` — response `{"token": ..., "status": "pending"}`.
  5. Если `status == 'dead'` — response `{"token": ..., "status": "dead"}`.
  6. Если запись не найдена — `404 {"detail": "Token not found"}`.
- HTTP status: `200` во всех случаях, кроме 404 (нет записи).
- Response model (из ARCHITECT §14.3):
  ```python
  class IndexStatusResponse(BaseModel):
      token: str
      status: Literal["pending", "done", "dead"]
  ```
- Endpoint не должен быть heavy: один SQL-запрос, без вычислений.
- Rate limiting: 10 requests/sec per IP (на Critical — stub через `slowapi` или просто `Counter` в Redis, не идеально но достаточно).
- Cache-control: `Cache-Control: no-store` (статус меняется, не кэшировать).
- Логирование: каждый запрос логируется с `token`, `status`, `latency_ms` (не более 1 мс).

### References

- **ARCHITECT §14.3** — точный API-контракт.
- **ARCHITECT §4.5** — read-your-writes SLO 5 сек, polling механизм.
- **ARCHITECT §6.7 (ТРИЗ)** — Принцип 27 (Дешёвая недолговечность): короткоживущий токен вместо long-polling.
- **ROADMAP §4.2.7** — `GET /index/status/{token}`, SLO 5 сек.
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §4.4 (DoD)** — «Polling /index/status/{token} корректно сообщает pending → done».
- **ROADMAP §4.5 (NFR)** — read-your-writes SLO 5 сек.

### Acceptance Criteria

- `GET /index/status/{valid_token_done}` → `200 {"token": "...", "status": "done"}`.
- `GET /index/status/{valid_token_pending}` → `200 {"token": "...", "status": "pending"}`.
- `GET /index/status/{valid_token_dead}` → `200 {"token": "...", "status": "dead"}`.
- `GET /index/status/{nonexistent_uuid}` → `404 {"detail": "Token not found"}`.
- `GET /index/status/{invalid_uuid_string}` → `422` (FastAPI валидация).
- `POST /index` → получить `wait_for_index_token` → `GET /index/status/{token}` (через 0 сек) → `status="pending"` → повторить через 30 сек → `status="done"`.
- Latency < 5 мс (один SQL, без вычислений).
- `Cache-Control: no-store` в response headers.
- Интеграционный тест: full cycle — index → poll until done (≤ 35 сек с reconciler).

### Forbidden patterns

- Не использовать long-polling или WebSocket — только polling (на Critical достаточно).
- Не возвращать `200` для несуществующего token — `404` (клиент должен знать, что токен не зарегистрирован).
- Не возвращать полный outbox-запись — только `token` и `status`.
- Не кэшировать response — статус меняется.
- Не делать heavy joins — один SELECT по `document_id` (есть индекс `search_outbox_doc_idx`).

### Suggested files

- `app/api/routes/index.py` — добавить роут `index_status`
- `app/api/schemas.py` — `IndexStatusResponse`
- `app/db/queries/outbox.py` — `async def get_status_by_document_id(doc_id: UUID) -> str | None`
- `tests/integration/test_wait_for_index_test.py`

---

## C-09 — Degraded mode: lex-only fallback + `partial_result()` + feature flag

> **Компонент ROADMAP §4.2.8, §4.3** | **Артефакт**: обновление `app/search/orchestrator.py` + `app/config.py::FeatureFlags`

### Context

Qdrant — единственная SPOF для векторного канала. Без safety net падение Qdrant парализует весь поиск. Degraded mode (TRIZ-gate §9 + §16) — заранее подложенный fallback: при недоступности Qdrant API автоматически переключается на lex-only, отдаёт результаты с `degraded: true`. При `DeadlineExceeded` — `partial_result()` отдаёт то, что успели посчитать (5 результатов за 100 мс лучше, чем 0 за 200 мс).

### Goal

1. Реализовать feature flag `vector_search_enabled` в `FeatureFlags` (C-00).
2. Реализовать health-check для Qdrant (если уже есть в P-12 `check_qdrant` — переиспользовать).
3. В `SearchOrchestrator.search()`:
   - Если `vector_search_enabled == False` или Qdrant health-check fail — skip векторного канала, только lex.
   - Если `vec_task` падает с `QdrantUnavailableException` — catch, skip, отдать lex-only.
   - Если `deadline` превышен — `partial_result()` отдаёт то, что успели из `lex_task` (даже если он не завершён, через `asyncio.wait` с `FIRST_COMPLETED`).
4. В response: `degraded: true` при lex-only, `partial: true` при partial_result.

### Constraints

- Feature flag `vector_search_enabled` (default `True`):
  - Если `False` — `vec_task` не запускается вообще, только `lex_task`.
  - Меняется в рантайме через `app/config.py` env override (без редеплоя).
- Qdrant health-check (из P-12 `check_qdrant`):
  - Использует `qdrant_client.get_collection_info("documents")` с timeout 100 мс.
  - Если fail — `SearchOrchestrator` логирует warning, skip векторного канала.
- `QdrantUnavailableException` (из MVP P-10 `app/search/exceptions.py`):
  - Catch в `SearchOrchestrator.search()`.
  - `total_vector = 0`, `hits` только из lex, `degraded = True`.
- `DeadlineExceeded` handling:
  - При `time.monotonic() > deadline` — `asyncio.wait([lex_task, vec_task], timeout=0, return_when=FIRST_COMPLETED)`.
  - Если `lex_task` done, `vec_task` pending — отдаём lex hits, `partial=True`.
  - Если оба pending — `partial_result()` возвращает `hits=[]`, `total_lexical=0`, `total_vector=0`, `degraded=True`, `partial=True`.
- `partial: bool` — новое поле в `SearchResponse` (опциональное, default `False`).
- Логирование: `logger.warning("Degraded mode activated", extra={"reason": "qdrant_unavailable"/"deadline_exceeded", "vector_search_enabled": ...})`.
- При recovery Qdrant (health-check снова ok) — `vector_search_enabled` автоматически не включается (только manual), но orchestrator снова запускает `vec_task` если flag=True.
- Метрики: counter `search_degraded_total{reason="qdrant_unavailable|deadline_exceeded|vector_disabled"}`, counter `search_partial_total`.

### References

- **ARCHITECT §14.1** — `degraded` flag в `SearchResponse`, `partial_result()` pseudocode.
- **ARCHITECT §4.6** — что делать при длительной недоступности Qdrant (feature flag).
- **ARCHITECT §6.7 (ТРИЗ)** — Принцип 9 (Предварительное антидействие): заранее подложенный fallback; Принцип 16 (Частичное/избыточное действие): partial_result.
- **ROADMAP §4.2.8** — degraded mode, lex-only fallback, partial_result.
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §4.4 (DoD)** — «API остаётся доступным при принудительном падении Qdrant».
- **ROADMAP §4.6** — риск «Qdrant недоступен»: mitigation — degraded mode.

### Acceptance Criteria

- `POST /search` при `vector_search_enabled=False` — `degraded=True`, `total_vector=0`, `hits` из lex.
- `POST /search` при работающем Qdrant — `degraded=False`, оба канала работают.
- `POST /search` при убитом Qdrant (`docker-compose stop qdrant`) — `degraded=True`, `total_vector=0`, `hits` из lex, `200 OK` (не 5xx).
- `POST /search` при `timeout_ms=10` — `partial=True`, `hits` либо пустой, либо пара результатов из lex (что успели).
- `POST /search` при убитом Qdrant и `timeout_ms=10` — `degraded=True`, `partial=True`, `hits=[]` (или 1-2 если lex успел).
- После recovery Qdrant (`docker-compose start qdrant`) — следующий `POST /search` работает в нормальном режиме (если `vector_search_enabled=True`).
- Метрики `search_degraded_total`, `search_partial_total` инкрементируются.
- Chaos-тест: kill Qdrant → 100 запросов `POST /search` → все возвращают `200` с `degraded=True`, нет 5xx.

### Forbidden patterns

- Не возвращать `5xx` при падении Qdrant — только `200` с `degraded=True`.
- Не блокировать event loop на health-check — `asyncio.wait_for(qdrant_client.get_collection_info(...), timeout=0.1)`.
- Не использовать `vector_search_enabled=False` как default — только при явном сбое или ручном override.
- Не забывать `partial=True` при deadline exceeded — клиент должен знать, что результат неполный.
- Не падать, если `vec_task` отменён через `asyncio.wait` — catch `CancelledError`.
- Не использовать `time.time()` — только `time.monotonic()`.

### Suggested files

- `app/search/orchestrator.py` — обновить `SearchOrchestrator.search()`, добавить `partial_result()`
- `app/api/schemas.py` — расширить `SearchResponse` полем `partial: bool = False`
- `app/config.py` — `FeatureFlags.vector_search_enabled` (если ещё нет в C-00)
- `app/observability/metrics.py` — `search_degraded_total`, `search_partial_total` counters
- `tests/integration/test_degraded_mode_test.py` — kill Qdrant, проверка response

---

## C-10 — Push-down фильтры (basic, по `reltuples`)

> **Компонент ROADMAP §4.2.9, §4.3** | **Артефакт**: `app/search/pushdown.py`

### Context

Payload-фильтр Qdrant на `tenant_id` + `attributes.category='ML'` на больших коллекциях может быть медленным (HNSW + post-filter). Push-down (TRIZ-gate §17 «Переход в другое измерение») — выполняем фильтр в Postgres сначала (где есть GIN-индексы), получаем список `doc_id`, передаём его в Qdrant как `MatchAny` filter на `doc_id`. Qdrant обходит HNSW только по этим кандидатурам — 3–5× ускорение.

### Goal

Реализовать `async def maybe_pushdown(tenant_id: UUID, filters: SearchFilters, threshold: float = None) -> list[UUID] | None` в `app/search/pushdown.py`. Интегрировать в `SearchOrchestrator.search()` перед запуском `vec_task`.

### Constraints

- Логика:
  1. Оценить селективность фильтров: `selectivity_estimate = expected_rows / total_rows_for_tenant`.
  2. Получить `total_rows` из `pg_class.reltuples` для `documents` (или кэш в Redis с TTL 5 минут).
  3. Получить `expected_rows` через `EXPLAIN` (без выполнения) или через быстрый `SELECT count(*)` с лимитом по времени.
  4. Если `selectivity_estimate < PushdownConfig.selectivity_threshold` (0.1) — выполнить push-down:
     - `SELECT id FROM documents WHERE tenant_id=$1 AND <filters> AND deleted_at IS NULL LIMIT $2` (max 5000).
     - Вернуть список `doc_id`.
  5. Если `selectivity_estimate >= threshold` или список пуст — вернуть `None` (использовать стандартный payload-filter).
  6. Если `len(doc_ids) > PushdownConfig.max_candidate_ids` (5000) — тоже вернуть `None` (push-down неэффективен на больших списках, Qdrant MatchAny деградирует).
- Интеграция в `vector_search()`:
  - Если `pushdown_ids` не `None` — добавить `Filter.must.append(FieldCondition(key="doc_id", match=MatchAny(any=pushdown_ids)))`.
  - Tenant_id всё равно в filter (двойная проверка).
- Метрики: counter `pushdown_total{result="used|skipped|too_large"}`, gauge `pushdown_selectivity` (последнее значение).
- Cache: `total_rows` по `tenant_id` кэшируется в Redis с TTL 5 мин (`pushdown:tenant_rows:{tenant_id}`).
- `EXPLAIN` вместо `count(*)`: для высокого `pg_class.reltuples` (10M+) `count(*)` дорогой, используем `EXPLAIN (FORMAT JSON)` и парсим `Plan.Rows`.
- Не применять push-down, если фильтров нет вообще (`SearchFilters` пустой) — селективность ~1.0, push-down бесполезен.

### References

- **ARCHITECT §8.2** — push-down спецификация, SQL пример, эвристика выбора.
- **ARCHITECT §8.7 (ТРИЗ)** — Принцип 17 (Переход в другое измерение): Postgres как третий агент в фильтрации.
- **ROADMAP §4.2.9** — push-down basic, по `reltuples`, 3–5× ускорение.
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §4.4 (DoD)** — «Push-down фильтры работают: latency ≤ 50 мс vs 200 мс без push-down на селективном фильтре».
- **ROADMAP §4.6** — риск «High-cardinality tenant фильтры»: mitigation — push-down.

### Acceptance Criteria

- `maybe_pushdown(tenant, SearchFilters(attributes={"category": "ML"}))` при 10k документов и 100 с category=ML → возвращает список 100 UUID.
- `maybe_pushdown(tenant, SearchFilters())` (пустой) → возвращает `None`.
- `maybe_pushdown(tenant, SearchFilters(attributes={"category": "very_rare"}))` при 0 совпадений → возвращает `[]` (push-down выполнен, но пустой) — `vec_search` пропускается.
- `maybe_pushdown(tenant, filters)` при `len(ids) > 5000` → возвращает `None` (слишком большой список, fallback на payload-filter).
- `selectivity_estimate = 0.05` (< 0.1) → push-down выполняется.
- `selectivity_estimate = 0.3` (>= 0.1) → push-down не выполняется, возвращает `None`.
- Векторный поиск с push-down — `qdrant_client.search` вызывается с `Filter.must` содержащим `MatchAny` на `doc_id`.
- Latency: на 100k документов, фильтр `category='ML'` (1k совпадений) — p99 ≤ 50 мс (vs 200 мс без push-down).
- Метрики `pushdown_total{result=...}` инкрементируются.

### Forbidden patterns

- Не использовать `SELECT count(*)` без `LIMIT` на больших таблицах — дорого. Лучше `EXPLAIN` или кэш `pg_class.reltuples`.
- Не применять push-down на неселективных фильтрах (language только, без category) — language обычно не селективный.
- Не хардкодить `selectivity_threshold` — через `PushdownConfig`.
- Не кэшировать список `doc_ids` в Redis — он меняется при индексации, кэш только для `total_rows`.
- Не передавать `doc_ids` в Qdrant как `MatchAny` если их > 5000 — Qdrant деградирует.
- Не использовать `IN (...)` в Postgres SQL — только `= ANY($1::uuid[])` (parameter binding).
- Не применять push-down, если `model_name` filter активен (он и так эффективен).

### Suggested files

- `app/search/pushdown.py` (`async def maybe_pushdown`, `class PushdownDecision`)
- `app/search/vector.py` — интегрировать push-down в `vector_search`
- `app/search/orchestrator.py` — вызывать `maybe_pushdown` перед `vector_search`
- `app/db/queries/documents.py` — `async def fetch_filtered_doc_ids(tenant_id, filters, limit) -> list[UUID]`
- `app/db/queries/tenants.py` — `async def get_tenant_doc_count(tenant_id) -> int` (с кэшем Redis)
- `app/observability/metrics.py` — `pushdown_total`, `pushdown_selectivity`
- `tests/unit/test_pushdown_test.py` — selectivity logic
- `tests/integration/test_pushdown_integration_test.py` — end-to-end на testcontainers

---

## C-11 — Адаптивный throttle при переполнении outbox (202 Accepted)

> **Компонент ROADMAP §4.2.10, §4.3** | **Артефакт**: обновление `app/services/indexing.py` + `app/services/throttle.py`

### Context

При длительном сбое Qdrant outbox переполняется pending-записями. Если не регулировать приём, PG забивается, latency индексации растёт, cascade-эффект парализует систему. Throttle (TRIZ-gate §21 «Проскочить») — при `pending_count > 50k` API отвечает `202 Accepted` вместо `201 Created`, сигнализируя клиенту «принято, но индексация задерживается». При `pending_count > 100k` — триггер full reindex (заглушка, реальный reindex в Production-Ready §5.2.7).

### Goal

1. Реализовать `class OutboxThrottle` с методами:
   - `async def get_pending_count(self) -> int` — кэшированный count (TTL 5 сек в Redis).
   - `async def should_throttle(self) -> bool` — `True` если `pending_count > 50k`.
   - `async def should_reindex(self) -> bool` — `True` если `pending_count > 100k`.
2. Обновить `IndexingService.create()`:
   - После создания outbox-записи — check throttle.
   - Если `should_throttle()` — response `202 Accepted` + `status="throttled"`.
   - Если `should_reindex()` — логировать critical warning, метрику `outbox_reindex_triggered` (сам reindex — stub, не реализовывать в Critical).
3. HTTP status code: `201` (normal) или `202` (throttled).

### Constraints

- `ThrottleConfig` (из C-00):
  - `pending_warn_threshold: int = 50000` — 202 Accepted
  - `pending_reindex_threshold: int = 100000` — full reindex trigger
- `pending_count` SQL:
  ```sql
  SELECT count(*) FROM search_outbox
  WHERE status IN ('pending', 'failed', 'in_progress')
    AND next_retry_at <= now() + interval '1 minute';
  ```
  (включая in_progress, потому что они тоже «в очереди»)
- Кэш `pending_count` в Redis (`throttle:pending_count`) с TTL 5 сек — чтобы не делать SQL на каждый `POST /index`.
- `IndexResponse.status` расширить: `"queued" | "throttled"` (без `"indexed"` на Critical, `indexed` только если wait_for_index или в Production-Ready).
- HTTP status: `201 Created` (normal) или `202 Accepted` (throttled) — оба валидны, клиент должен проверять `status` поле.
- При `should_reindex()`:
  - Лог: `logger.critical("Outbox overflow - full reindex triggered", extra={"pending_count": N})`.
  - Метрика: counter `outbox_reindex_triggered_total` (инкремент).
  - Сам reindex: stub — запись в `app/services/throttle.py::trigger_full_reindex()` только логирует, не запускает. Реальная реализация в Production-Ready §5.2.7.
- В `IndexResponse` добавить поле `throttled: bool = False` для удобства клиента.
- Метрики: gauge `outbox_pending_count`, counter `outbox_throttled_total`, counter `outbox_reindex_triggered_total`.

### References

- **ARCHITECT §4.6** — адаптивный throttle, 202 Accepted, watermark 100k → full reindex.
- **ARCHITECT §4.7 (ТРИЗ)** — Принцип 21 (Проскочить): 202 вместо блокировки.
- **ROADMAP §4.2.10** — `pending_count > 50k` → 202, `> 100k` → full reindex.
- **ROADMAP §4.3** — TRIZ-gate Critical.
- **ROADMAP §4.4 (DoD)** — явно не упоминается, но является частью DoD §4.4 «API остаётся доступным».
- **ROADMAP §4.6** — риск «Outbox переполняется»: mitigation — throttle + reindex.
- **ROADMAP §12.2 (Operational risks)** — `High pending_count in outbox`: mitigation — adaptive throttle → full reindex.

### Acceptance Criteria

- `OutboxThrottle.get_pending_count()` при пустом outbox — `0`.
- `OutboxThrottle.get_pending_count()` при 100k pending — `100000` (с кэша Redis).
- `POST /index` при `pending_count=1000` — `201 Created`, `status="queued"`, `throttled=False`.
- `POST /index` при `pending_count=60000` — `202 Accepted`, `status="throttled"`, `throttled=True`.
- `POST /index` при `pending_count=150000` — `202 Accepted`, `status="throttled"`, `throttled=True`, в логах `outbox_reindex_triggered`.
- Повторный `POST /index` через 5 сек (кэш TTL истёк) — пересчитывает `pending_count`.
- Метрики `outbox_pending_count` (gauge), `outbox_throttled_total` (counter), `outbox_reindex_triggered_total` (counter) публикуются.
- Тест: вставить 60k pending записей в outbox (через `INSERT ... SELECT generate_series(...)`) → следующий `POST /index` возвращает `202`.

### Forbidden patterns

- Не блокировать `POST /index` при throttle — только менять status code на `202` и `status` поле.
- Не запускать full reindex автоматически в Critical — только stub с логом и метрикой.
- Не хардкодить `50000` и `100000` — через `ThrottleConfig`.
- Не делать `SELECT count(*)` на каждый `POST /index` — кэш в Redis с TTL 5 сек.
- Не возвращать `503` — это для случая «совсем не работает», а у нас «работает медленно».
- Не забывать логировать `pending_count` при throttle — для отладки.

### Suggested files

- `app/services/throttle.py` (`class OutboxThrottle`)
- `app/services/indexing.py` — обновить `IndexingService.create()` с throttle check
- `app/api/routes/index.py` — обновить response status code (201 vs 202)
- `app/api/schemas.py` — `IndexResponse.status: Literal["queued", "throttled"]`, `IndexResponse.throttled: bool = False`
- `app/db/queries/outbox.py` — `async def count_pending() -> int`
- `app/observability/metrics.py` — `outbox_pending_count`, `outbox_throttled_total`, `outbox_reindex_triggered_total`
- `tests/unit/test_throttle_test.py`

---

## C-12 — Observability additions: метрики Critical фазы

> **Компонент ROADMAP §4.5, §11.1** | **Артефакт**: расширение `app/observability/metrics.py`

### Context

MVP дал 5 базовых метрик (P-15). Critical добавляет ещё ~6 метрик для мониторинга quality-слоя: circuit breaker state, push-down rate, fusion strategy usage, eval metrics, throttling, degraded mode. Без этих метрик команда слепа к поведению reranker'а и push-down'а.

### Goal

Расширить `app/observability/metrics.py` метриками Critical фазы. Интегрировать их в соответствующие компоненты (C-03 circuit breaker, C-07 eval, C-10 pushdown, C-11 throttle, C-09 degraded).

### Constraints

- Новые метрики (из ARCHITECT §11.1, ROADMAP §4.5):
  1. `circuit_breaker_state` — Gauge, labels: `component="reranker"`. Values: 0=closed, 1=half_open, 2=open. Заполняется в C-03.
  2. `circuit_breaker_opened_total` — Counter, labels: `component`, `reason`. Заполняется в C-03.
  3. `circuit_breaker_requests_total` — Counter, labels: `component`, `result` (success/rejected/error). Заполняется в C-03.
  4. `qdrant_pushdown_rate` — Counter, labels: `result` (used/skipped/too_large). Заполняется в C-10.
  5. `pushdown_selectivity` — Gauge (последнее значение selectivity_estimate). Заполняется в C-10.
  6. `fusion_strategy_usage` — Counter, labels: `strategy` (rrf/weighted/rerank/weighted+rerank). Заполняется в C-05 (orchestrator).
  7. `eval_recall_at_10` — Gauge, labels: `strategy`, `dataset_version`. Заполняется в C-07.
  8. `eval_ndcg_at_10` — Gauge, labels: `strategy`, `dataset_version`. Заполняется в C-07.
  9. `eval_mrr` — Gauge, labels: `strategy`, `dataset_version`. Заполняется в C-07.
  10. `outbox_pending_count` — Gauge. Заполняется в C-11.
  11. `outbox_throttled_total` — Counter. Заполняется в C-11.
  12. `outbox_reindex_triggered_total` — Counter. Заполняется в C-11.
  13. `search_degraded_total` — Counter, labels: `reason`. Заполняется в C-09.
  14. `search_partial_total` — Counter. Заполняется в C-09.
  15. `reranker_latency_ms` — Histogram, labels: `device` (cuda/cpu), `mock` (true/false). Buckets: `[10, 25, 50, 100, 200, 300, 500, 1000]`. Заполняется в C-01.
- Все метрики регистрируются в singleton `METRICS` (как в MVP P-15).
- Labels: только cardinality-safe (`strategy`, `device`, `result`, `reason`). Никаких `tenant_id` (high cardinality) — tenant-breakdown через query_log, не через Prometheus labels.
- Документация: обновить `app/observability/__init__.py` docstring с полным списком метрик и их источником.

### References

- **ARCHITECT §11.1** — полный список метрик (включая future).
- **ARCHITECT §11.8 (ТРИЗ)** — Принцип 17: три измерения observability (metrics ↔ logs ↔ traces), здесь добавляем метрики; traces — Production-Ready.
- **ROADMAP §4.5 (NFR)** — какие метрики нужны для SLO.
- **ROADMAP §11.1 (метрики прогресса)** — `circuit_breaker_open`, `qdrant_pushdown_rate` etc.
- **ROADMAP §13.2** — список всех quality-метрик по фазам.

### Acceptance Criteria

- `GET /metrics` содержит все 15 новых метрик.
- После `POST /search` с `fusion="weighted"` — `fusion_strategy_usage{strategy="weighted"}` инкрементирован.
- После открытия circuit breaker — `circuit_breaker_state{component="reranker"}` = 2, `circuit_breaker_opened_total` инкрементирован.
- После push-down выполнения — `qdrant_pushdown_rate{result="used"}` инкрементирован.
- После nightly eval — `eval_recall_at_10{strategy="rrf",dataset_version="v1"}` updated.
- После `POST /index` при throttled — `outbox_throttled_total` инкрементирован, `outbox_pending_count` gauge updated.
- После degraded mode — `search_degraded_total{reason="qdrant_unavailable"}` инкрементирован.
- Unit-тест проверяет, что singleton `METRICS` содержит все метрики (через `prometheus_client.REGISTRY`).

### Forbidden patterns

- Не использовать high-cardinality labels (`tenant_id`, `user_id`, `query_hash`) — раздувают Redis/Prometheus.
- Не регистрировать метрики в каждом компоненте — только в singleton `METRICS` (через `app/observability/metrics.py`).
- Не использовать `Counter().inc()` без labels если метрика имеет labels — нужно `Counter(labels=...).labels(...).inc()`.
- Не публиковать `eval_recall_at_10` если eval ещё не запускался — gauge должен быть `NaN` или `0`, но не падать.
- Не добавлять trace_id как label — это log/traces domain, не metrics.

### Suggested files

- `app/observability/metrics.py` — расширить singleton `METRICS`
- `app/observability/__init__.py` — обновить docstring
- `tests/unit/test_metrics_test.py` — обновить с новыми метриками

---

## C-13 — Grafana Critical dashboards

> **Компонент ROADMAP §4.5, §11.1** | **Артефакт**: `grafana/provisioning/dashboards/critical-*.json`

### Context

MVP дал 4 базовых дашборда (P-16). Critical добавляет 4 dashboard для нового quality-слоя: reranker latency/circuit breaker, eval regression, push-down rate, throttle. Без визуализации команда не увидит, что circuit breaker открыт или nightly eval поймал regression.

### Goal

Создать 4 Grafana JSON-дашборда в `grafana/provisioning/dashboards/`:
1. `critical-reranker.json` — reranker latency, circuit breaker state, mock_mode indicator.
2. `critical-eval-regression.json` — recall@10/nDCG@10/MRR по стратегиям, regression alerts.
3. `critical-pushdown.json` — push-down rate, selectivity distribution, latency comparison.
4. `critical-circuit-breaker.json` — circuit breaker state over time, open events, request flow.

### Constraints

- DataSource: Prometheus (как в MVP P-16).
- Dashboards:
  1. **Critical — Reranker** (`critical-reranker.json`):
     - Panel 1: Latency p50/p95/p99 (`histogram_quantile(0.5, rate(reranker_latency_ms_bucket{device="cuda"}))` и т.д.).
     - Panel 2: Circuit breaker state (`circuit_breaker_state{component="reranker"}`).
     - Panel 3: Mock mode indicator (если `reranker_latency_ms{mock="true"}` — алерт «dev режим»).
     - Panel 4: Throughput (`rate(circuit_breaker_requests_total{result="success"}[5m])`).
  2. **Critical — Eval Regression** (`critical-eval-regression.json`):
     - Panel 1: Recall@10 по стратегиям (`eval_recall_at_10{strategy="rrf"}`, `=weighted`, `=weighted+rerank` — три линии).
     - Panel 2: nDCG@10 по стратегиям.
     - Panel 3: MRR по стратегиям.
     - Panel 4: Last run timestamp (через `eval_last_run_timestamp` gauge, если добавить в C-12 — иначе через `changes(eval_recall_at_10[1d])`).
     - Alert: recall drop > 2% за день → Pending → Firing.
  3. **Critical — Push-down** (`critical-pushdown.json`):
     - Panel 1: Push-down rate (`rate(qdrant_pushdown_rate{result="used"}[5m]) / rate(qdrant_pushdown_rate[5m])`).
     - Panel 2: Selectivity distribution (`pushdown_selectivity` gauge over time).
     - Panel 3: Latency comparison: push-down vs no-push-down (нужна метрика `vector_search_latency_ms` с label `pushdown=true|false` — добавить в C-12 если нет, либо использовать existing `search_latency_ms` с extra-label через `debug`).
     - Panel 4: Push-down too_large ratio (`rate(qdrant_pushdown_rate{result="too_large"}[5m])`).
  4. **Critical — Circuit Breaker & Throttle** (`critical-circuit-breaker.json`):
     - Panel 1: Circuit breaker state over time (`circuit_breaker_state`).
     - Panel 2: Open events (`rate(circuit_breaker_opened_total[1h])`).
     - Panel 3: Outbox pending count (`outbox_pending_count`).
     - Panel 4: Throttle rate (`rate(outbox_throttled_total[5m])`).
     - Alert: circuit_breaker_state == 2 > 60s → Firing.
- Provisioning: использовать существующий `grafana/provisioning/dashboards/dashboards.yml` из MVP P-16 — он автоматически подхватит новые JSON.
- Переменные: `${DS_PROMETHEUS}` для datasource, `${tenant}` для фильтра (если есть tenant-label — но мы избегаем high-cardinality labels).
- `docker-compose.yml` уже включает Grafana из MVP — обновлять не нужно.

### References

- **ARCHITECT §11.1** — список метрик.
- **ROADMAP §4.5 (NFR)** — что мониторить.
- **ROADMAP §11.1** — дашборды для SLO.
- **MVP-PROMPTS.md P-16** — формат JSON-дашбордов.

### Acceptance Criteria

- `docker-compose up` поднимает Grafana с 8 дашбордами (4 MVP + 4 Critical).
- Все 4 новых дашборда автоматически появляются (через provisioning).
- На «Critical — Reranker» после нескольких `POST /search` с `rerank=true` — видны ненулевые значения latency.
- На «Critical — Eval Regression» после `python -m app.eval.nightly` — видны 3 линии recall@10.
- На «Critical — Push-down» после селективных запросов — `result="used"` инкрементирован.
- На «Critical — Circuit Breaker & Throttle» после открытия breaker — state = 2.
- Alert «circuit_breaker_open > 60s» — переходит в Pending → Firing.
- Alert «recall drop > 2%» — срабатывает при regression.

### Forbidden patterns

- Не хардкодить URL Prometheus — `${DS_PROMETHEUS}` variable.
- Не использовать deprecated Grafana JSON-схему (v1) — только v2.
- Не публиковать admin-пароль — через env.
- Не добавлять дашборды из Production-Ready (OTel traces, A/B) — они отдельно.
- Не использовать high-cardinality panel variables (`$tenant_id` free input) — только предопределённые значения или `*`.

### Suggested files

- `grafana/provisioning/dashboards/critical-reranker.json`
- `grafana/provisioning/dashboards/critical-eval-regression.json`
- `grafana/provisioning/dashboards/critical-pushdown.json`
- `grafana/provisioning/dashboards/critical-circuit-breaker.json`
- `app/observability/metrics.py` — при необходимости добавить `eval_last_run_timestamp` gauge

---

## C-14 — E2E-тесты и приёмка DoD Critical

> **Компонент ROADMAP §4.4, §4.5** | **Artefact**: `tests/e2e/test_critical_dod.py` + `scripts/run_dod_check_critical.sh`

### Context

Финальный промпт фазы Critical. Цель — формально проверить Definition of Done из ROADMAP §4.4 и NFR-таргеты из §4.5. Если все 6 DoD-критериев зелёные — Critical готов к переходу в Production-Ready (ROADMAP §9.2 exit criteria).

### Goal

Реализовать E2E-test suite `tests/e2e/test_critical_dod.py`, покрывающий все 6 пунктов DoD из ROADMAP §4.4 + NFR-замеры через `scripts/run_dod_check_critical.sh`.

### Constraints

- **DoD 1**: «Cross-encoder поднимает nDCG@10 на 5–10% относительно RRF baseline на eval-датасете».
  - Тест: запустить `NightlyEvalJob` (C-07), сравнить `eval_ndcg_at_10{strategy="weighted+rerank"}` и `eval_ndcg_at_10{strategy="rrf"}` — разница ≥ 5%.
  - Если mock_mode — skip (нет GPU).
- **DoD 2**: «Circuit breaker автоматически отключает rerank при инъекции искусственной деградации».
  - Тест: 100 запросов с `rerank=true`, инжектировать 6 ошибок (5% + 1) в `RerankerService` (через mock, возвращающий `RerankerUnavailableException` для 6% запросов) → проверить `circuit_breaker_state == 2`.
  - После отключения — `POST /search` с `rerank=true` отдаёт `debug.rerank_degraded=True`, без вызова reranker.
- **DoD 3**: «Nightly eval ловит регрессии и блокирует релиз при превышении threshold».
  - Тест: запустить `NightlyEvalJob` с baseline, затем с деградированной стратегией (mock, возвращающий случайные doc_ids) → `EvalReport.regression == True`, `scripts/run_nightly_eval.sh` exit code 1.
- **DoD 4**: «API остаётся доступным при принудительном падении Qdrant — возвращает lex-only результаты с `degraded: true`».
  - Тест: `POST /index` (документы индексируются), `docker-compose stop qdrant`, `POST /search` → `200 OK`, `degraded=True`, `total_vector=0`, `hits` не пустые.
  - `docker-compose start qdrant`, ждать 30 сек, `POST /search` → `degraded=False`.
- **DoD 5**: «Polling `/index/status/{token}` корректно сообщает `pending → done` после индексации».
  - Тест: `POST /index` → `GET /index/status/{token}` (сразу) → `status="pending"`, повторять каждые 2 сек → ≤ 35 сек `status="done"`.
  - При инъекции dead-записи (`embedding_model='nonexistent'`) → после 20 попыток `status="dead"`.
- **DoD 6**: «Push-down фильтры работают: на селективном фильтре (`category='ML' AND created_after > '2026-01-01'`) latency ≤ 50 мс vs 200 мс без push-down».
  - Тест: вставить 100k документов, 1k с `category='ML'` + `created_at > 2026-01-01`.
  - `POST /search` с `filters.attributes.category="ML"`, `filters.created_after=2026-01-01` — замерить latency 100 раз, p99 ≤ 50 мс.
  - Дезейблить push-down через `FeatureFlags.pushdown_enabled=False` — p99 > 150 мс (baseline без push-down).
- **NFR-замеры** (через `scripts/run_dod_check_critical.sh`):
  1. Latency p99 `/search` без rerank ≤ 150 мс — 1000 запросов, p99 через `numpy.percentile`.
  2. Latency p99 `/search` с rerank top-50 ≤ 350 мс — 1000 запросов с `rerank=true` (если GPU доступен, иначе skip).
  3. Throughput индексации ≥ 1000 doc/min — 100 параллельных `POST /index`.
  4. Recall@10 не падает > 2% относительно baseline (nightly eval).
  5. nDCG@10 не падает > 1% относительно baseline.
  6. `embedding_cache_hit_rate` ≥ 50% — из `/metrics`.
  7. SLA 99.5% — `successful_requests / total_requests` за 10 минут.
  8. Lag ≤ 10 секунд — `POST /index` → polling `/index/status/{token}` → время до `status="done"`.
  9. Read-your-writes SLO 5 сек — `POST /index` → `POST /search` раз в 1 сек → документ появляется ≤ 5 сек.
- Скрипт выводит таблицу: `DoD # | Описание | Статус | Значение | Целевое | Pass/Fail`.
- Exit code 0 — все pass, 1 — хотя бы один fail.

### References

- **ROADMAP §4.4** — Definition of Done (6 пунктов).
- **ROADMAP §4.5** — NFR-таргеты (9 параметров).
- **ROADMAP §9.2** — exit criteria Critical → Production-Ready.
- **ARCHITECT §3.4, §3.5** — implicit reference (DoD синхронизирован).

### Acceptance Criteria

- `pytest tests/e2e/test_critical_dod.py -v` — все 6 DoD-тестов зелёные (с skip для GPU-зависимых при отсутствии CUDA).
- `bash scripts/run_dod_check_critical.sh` — exit code 0, все 9 NFR-замеров `Pass`.
- Тесты запускаются против `docker-compose up` (полный стек: postgres, qdrant, redis, api, reconciler, digest, grafana).
- Время выполнения всего suite ≤ 15 минут (включая chaos-сценарии kill Qdrant + 60с ожидания).
- Тесты идемпотентны: `autouse` fixture с `TRUNCATE documents, search_outbox, search_outbox_dead_digest, eval_results` между тестами.
- DoD 1 (nDCG improvement): если mock_mode — skip с `pytest.skip("requires GPU for reranker")`.
- DoD 6 (push-down): если 100k документов не создаются (memory constraint в CI) — skip с `pytest.skip("requires large dataset")`.

### Forbidden patterns

- Не использовать `time.sleep(60)` без объяснения — только `poll_until(predicate, timeout, interval)`.
- Не запускать reconciler/digest/nightly вручную — через `docker-compose up` или фоновые процессы в `conftest.py`.
- Не оставлять мусор в БД — `autouse` fixture с `TRUNCATE`.
- Не использовать production-данные — синтетические seed'ы (100k документов через `generate_series`).
- Не падать при flaky-таймаутах — `pytest-rerunfailures` для DoD 4 (kill Qdrant, 1 rerun).
- Не требовать GPU в CI по умолчанию — `@pytest.mark.gpu`, skip если нет.

### Suggested files

- `tests/e2e/test_critical_dod.py` — 6 DoD-тестов
- `tests/e2e/conftest.py` — расширить (если нужно) фикстурами для Critical
- `scripts/run_dod_check_critical.sh`
- `scripts/run_nfr_load_critical.py` — NFR-замеры (locust или httpx + asyncio)
- `Makefile` / `tasks.py` — `make e2e-critical`, `make nfr-critical`, `make dod-critical`

---

## Приложение A. Чек-лист готовности Critical к сдаче

Перед запуском `C-14` убедиться, что выполнены:

- [ ] **C-00** — конфиг расширён, eval-датасет ≥100 запросов, env-переменные готовы.
- [ ] **C-01** — `RerankerService.rerank()` работает на GPU (или mock_mode), sigmoid score в [0, 1].
- [ ] **C-02** — `weighted_fuse()` с min-max нормализацией, `alpha` параметр.
- [ ] **C-03** — `RerankerCircuitBreaker` открывается при 5% errors / 500 мс p95, переходит в half_open после 60 сек.
- [ ] **C-04** — `SpeculativeReranker.run()` экономит ~80 мс на p99 (через mock-бенчмарк).
- [ ] **C-05** — `POST /search` с `fusion="weighted"`, `rerank=true`, `fusion_alpha=0.7` работает end-to-end.
- [ ] **C-06** — `facets` наполняется в response при `facets=["tags"]` в запросе.
- [ ] **C-07** — `python -m app.eval.nightly` отрабатывает, `eval_results` содержит 3 строки, regression detection работает.
- [ ] **C-08** — `GET /index/status/{token}` возвращает `pending → done` после индексации.
- [ ] **C-09** — `POST /search` при убитом Qdrant возвращает `200` с `degraded=True`, без 5xx.
- [ ] **C-10** — `maybe_pushdown` на селективном фильтре возвращает ≤5000 doc_ids, latency ≤ 50 мс.
- [ ] **C-11** — `POST /index` при `pending_count > 50k` возвращает `202 Accepted`.
- [ ] **C-12** — 15 новых метрик в `/metrics`.
- [ ] **C-13** — 4 новых Grafana-дашборда видны.
- [ ] **TRIZ-gates Critical** (ROADMAP §4.3 / §10.2) — все 7 встроены:
  - [ ] Circuit breaker для reranker (C-03)
  - [ ] Speculative rerank (C-04)
  - [ ] `wait_for_index_token` polling (C-08)
  - [ ] `degraded` flag + `partial_result()` (C-09)
  - [ ] Nightly eval как regression gate (C-07)
  - [ ] 202 Accepted при throttle (C-11)
  - [ ] Push-down фильтры (C-10)
- [ ] **Exit criteria §9.2** (ROADMAP) — все условия выполнены:
  - [ ] DoD Critical достигнут (§4.4).
  - [ ] NFR Critical достигнуты: p99 ≤ 350 мс с rerank, Recall не падает > 2%, nDCG не падает > 1%, SLA 99.5%.
  - [ ] Circuit breaker срабатывает автоматически при chaos-сценарии.
  - [ ] Nightly eval стабильно работает ≥ 2 недели без false-positive алертов.
  - [ ] `wait_for_index` polling подтверждает read-your-writes в SLO 5 сек.
  - [ ] Push-down фильтры демонстрируют 3–5× ускорение на селективных фильтрах.
  - [ ] Команда подтвердила готовность к более частым релизам (nightly eval как gate).

## Приложение B. Что НЕ делать в Critical (scope guard)

Список фич, которые **запрещено** реализовывать в этой фазе (они появятся в Production-Ready / Improvements):

- ❌ A/B-фреймворк (Production-Ready, ARCHITECT §11.3, ROADMAP §5.2.3)
- ❌ `experiment_id` распределение трафика (Production-Ready)
- ❌ Персонализация — `profile_vector` (Production-Ready, ARCHITECT §10, ROADMAP §5.2.1)
- ❌ MMR anti-bubble diversification (Production-Ready, ARCHITECT §10.5, ROADMAP §5.2.4)
- ❌ Контекстная персонализация (Production-Ready, ARCHITECT §10.6)
- ❌ Distributed tracing (OpenTelemetry → Tempo) (Production-Ready, ARCHITECT §11.2, ROADMAP §5.2.6)
- ❌ Shadow traffic (Production-Ready, ARCHITECT §11.6, ROADMAP §5.2.8)
- ❌ Chaos engineering weekly (Production-Ready, ARCHITECT §11.7, ROADMAP §5.2.9)
- ❌ Migration framework blue-green (Production-Ready, ARCHITECT §13, ROADMAP §5.2.7)
- ❌ Канареечный reindex (Production-Ready, ARCHITECT §13.5)
- ❌ Snapshot-on-reindex в S3 (Production-Ready, ARCHITECT §13.6)
- ❌ `tenant_search_stats` таблица (Production-Ready, ROADMAP §5.2.10)
- ❌ Adaptive K по tenant (Production-Ready, ROADMAP §5.2.10)
- ❌ Инкрементальные фасеты в Redis (Production-Ready, ROADMAP §5.2.11)
- ❌ Многоязычность enhanced: `fasttext-langid` (Production-Ready, ROADMAP §5.2.12)
- ❌ Смешанные запросы token-level (Production-Ready, ROADMAP §5.2.12)
- ❌ Асимметричное индексирование / chunking (Production-Ready, ROADMAP §5.2.13)
- ❌ Schema drift контракт-тесты (Production-Ready, ROADMAP §5.2.14) — добавляются в CI в любой момент, но в Critical не требуются
- ❌ Negative cache для embeddings (Production-Ready, ROADMAP §5.2.15)
- ❌ Learned ranking LambdaMART (Improvements, ARCHITECT §7.4, ROADMAP §6.2.1)
- ❌ Каскадный выбор стратегии (Improvements, ARCHITECT §7.5, ROADMAP §6.2.2)
- ❌ Multi-armed bandit для `α` (Improvements, ROADMAP §6.2.13)
- ❌ Тенантное tiering (Improvements, ARCHITECT §12.6, ROADMAP §6.2.3)
- ❌ PostgreSQL read replicas + PgBouncer (Improvements, ROADMAP §6.2.5)
- ❌ Qdrant sharding 4 → 8 (Improvements, ROADMAP §6.2.6)
- ❌ Redis Cluster (Improvements, ROADMAP §6.2.9)
- ❌ CDC migration Debezium (Improvements, ROADMAP §6.2.11)
- ❌ Connection-aware routing (Improvements, ROADMAP §6.2.10)

Если в процессе работы над Critical-промптом агент считает, что какая-то из этих фич «нужна прямо сейчас» — это красный флаг. Зафиксировать в `worklog.md` как «отложено до фазы X» и продолжить Critical.

---

*Документ сопровождает `ARCHITECT.md` v2.0 и `ROADMAP.md` v1.0. При изменении архитектуры — обновить промпты синхронно; при изменении NFR — обновить C-14.*
