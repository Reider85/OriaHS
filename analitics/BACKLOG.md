# BACKLOG.md — Сводный бэклог проекта OriaHS

> **Источник**: `ROADMAP.md` v1.0, `ARCHITECT.md` v2.0, `MVP-PROMPTS.md` v1.0, `CRITICAL-PROMPTS.md` v1.0
> **Назначение**: единое отслеживание всех задач по 4 фазам зрелости — MVP → Critical → Production-Ready → Improvements. Гарантирует, что ни один компонент из ROADMAP §7 (матрица фаз × компонентов) не потерян.
> **Методология**: 1 задача = 1 компонент ROADMAP (или supporting prompt). Задачи сгруппированы по фазам, имеют зависимости, ссылки на §ref, статусы.
> **Соглашение о статусах**: `done` (реализовано) / `in_progress` (в работе) / `pending` (не начато) / `blocked` (блокировано зависимостью) / `optional` (триггерный, можно пропустить)

---

## 0. Сводная карта

### 0.1. Количество задач по фазам

| Фаза | ROADMAP § | Компонентов (§13.1) | Промптов / задач | Статус фазы |
|---|---|---|---|---|
| **MVP** | §3 | 18 (с supporting) | 18 (P-00..P-17) | ✅ done (код в репозитории) |
| **Critical** | §4 | 10 (§4.2.1–4.2.10) | 15 (C-00..C-14) | ⏳ pending (промпты готовы) |
| **Production-Ready** | §5 | 15 (§5.2.1–5.2.15) | 15 (PR-01..PR-15) | ⏳ pending |
| **Improvements** | §6 | 13 (§6.2.1–6.2.13) | 13 (IM-01..IM-13) | ⏳ trigger-based |
| **Всего** | — | 56 компонентов | 61 задача | — |

### 0.2. TRIZ-gates по фазам

| Фаза | TRIZ-gates (§10) | Внедрено | Охват |
|---|---|---|---|
| MVP | 6 (§10.1) | 6 | 100% |
| Critical | 9 (§10.2) | 0 | 0% (внедряются в C-00..C-14) |
| Production-Ready | 15 (§10.3) | 0 | 0% |
| Improvements | 8 (§10.4) | 0 | 0% |
| **Всего** | **38** | **6** | **16%** |

### 0.3. NFR-таргенты по фазам (ROADMAP §11)

| Параметр | MVP | Critical | Production-Ready | Improvements (Tier A) |
|---|---|---|---|---|
| Latency p99 `/search` (без rerank) | ≤ 200 мс ✅ | ≤ 150 мс ⏳ | ≤ 120 мс ⏳ | ≤ 80 мс ⏳ |
| Latency p99 `/search` (с rerank) | — | ≤ 350 мс ⏳ | ≤ 350 мс ⏳ | ≤ 200 мс ⏳ |
| Throughput индексации (doc/min) | ≥ 500 ✅ | ≥ 1000 ⏳ | ≥ 2000 ⏳ | ≥ 5000 ⏳ |
| Throughput поиска (RPS/instance) | ≥ 100 ✅ | ≥ 200 ⏳ | ≥ 500 ⏳ | ≥ 500 (Tier A) ⏳ |
| SLA | 99% ✅ | 99.5% ⏳ | 99.9% ⏳ | 99.95% (Tier A) ⏳ |
| Lag Postgres↔Qdrant | ≤ 30 с ✅ | ≤ 10 с ⏳ | ≤ 5 с ⏳ | ≤ 2 с (после CDC) ⏳ |
| `embedding_cache_hit_rate` | ≥ 40% ✅ | ≥ 50% ⏳ | ≥ 60% ⏳ | ≥ 70% ⏳ |
| Recall@10 (vs baseline) | baseline ✅ | не падает > 2% ⏳ | не падает > 2% ⏳ | не падает > 2% ⏳ |
| nDCG@10 (vs baseline) | baseline ✅ | не падает > 1% ⏳ | не падает > 1% ⏳ | +8–15% (LambdaMART) ⏳ |
| Read-your-writes SLO | не гарантируется | 5 сек (polling) ⏳ | 5 сек ⏳ | 5 сек ⏳ |

### 0.4. Exit criteria между фазами (ROADMAP §9)

| Переход | Критерии | Статус |
|---|---|---|
| MVP → Critical (§9.1) | DoD MVP, NFR MVP, chaos Qdrant 60s, reconciler 100k <5min, cache ≥ 40%, eval-датасет ≥100 запросов | ✅ (требует формальной проверки в P-17, но код MVP готов) |
| Critical → Production-Ready (§9.2) | DoD Critical, NFR Critical, circuit breaker auto-activation, nightly eval 2 недели stable, wait_for_index 5с SLO, push-down 3–5× speedup, готовность к частым релизам | ⏳ блокировано задачами C-00..C-14 |
| Production-Ready → Improvements (§9.3) | DoD PR, NFR PR, ≥ 4 недели кликовых данных, OTel end-to-end, chaos weekly green, ≥ 1 zero-downtime миграция, trigger активирован | ⏳ блокировано задачами PR-01..PR-15 |

---

## 1. Фаза MVP — минимальный рабочий контур

> **ROADMAP §3** | **Промптов**: 18 (P-00..P-17) | **Статус**: ✅ done (код в `/app/`, `/tests/`, `/alembic/versions/`)

### 1.1. Задачи MVP

| ID | Компонент ROADMAP | Зависимости | ARCHITECT §ref | ROADMAP §ref | Артефакт | Статус |
|----|---|---|---|---|---|---|
| P-00 | Bootstrap (§3 вводная) | — | §2.1, §17.2 | §3 | `docker-compose.yml`, `pyproject.toml`, `app/` skeleton, `.env.example`, `alembic init` | ✅ done |
| P-01 | PG `documents` (§3.2.1) | P-00 | §3.1 | §3.2.1 | `alembic/versions/001_documents.py`, `app/db/models/document.py` | ✅ done |
| P-02 | PG `search_outbox` (§3.2.1, §3.2.5) | P-01 | §3.1, §4.4 | §3.2.1, §3.2.5 | `alembic/versions/002_outbox.py`, `app/db/models/outbox.py` | ✅ done |
| P-03 | PG `embedding_models` (§3.2.1, §3.3) | P-01 | §3.1, §5.4 | §3.2.1, §3.3 | `alembic/versions/003_embedding_models.py`, seed `bge-m3-v1` | ✅ done |
| P-04 | Qdrant collection (§3.2.2) | P-00 | §3.2, §17.1 | §3.2.2 | `qdrant_collections.yaml`, `scripts/init_qdrant.py`, `app/search/qdrant_payload.py` | ✅ done |
| P-05 | Embedding model bge-m3 (§3.2.3) | P-00, P-03 | §5.1, §5.2 | §3.2.3 | `app/embedding/service.py` (батчинг 32) | ✅ done |
| P-06 | Embedding cache Redis (§3.2.3, §3.3) | P-05 | §4.3, §5.3 | §3.2.3, §3.3 | `app/embedding/cache.py` (content-hash + query + fast-path) | ✅ done |
| P-07 | POST /index (§3.2.4) | P-01..P-03, P-06 | §2.4, §4.2, §14.2 | §3.2.4 | `app/api/routes/index.py` (dual-write + outbox + Redis Streams) | ✅ done |
| P-08 | DELETE /index/{doc_id} (§3.2.4) | P-01, P-02, P-07 | §14.4 | §3.2.4 | soft-delete + outbox `op='delete'` | ✅ done |
| P-09 | Lexical search (§3.2.4) | P-01 | §6.1, §8.1, §8.3 | §3.2.4 | `app/search/lexical.py` (ts_rank + pg_trgm + filters, K=50) | ✅ done |
| P-10 | Vector search (§3.2.4) | P-04, P-06 | §6.1, §8.3 | §3.2.4 | `app/search/vector.py` (Qdrant kNN + payload filter, K=50) | ✅ done |
| P-11 | POST /search orchestrator (§3.2.4) | P-09, P-10 | §2.3, §6.1, §7.1, §14.1 | §3.2.4 | `app/api/routes/search.py` + `app/search/fusion.py` (RRF k=60) | ✅ done |
| P-12 | Health + /metrics (§3.2.4, §3.2.6) | P-01..P-04 | §14.5, §11.1 | §3.2.4, §3.2.6 | `app/api/routes/health.py`, `/metrics`, readiness check | ✅ done |
| P-13 | Reconciler (§3.2.5) | P-02, P-04..P-06 | §4.4, §4.5 | §3.2.5 | `app/reconciler/worker.py` (30s poll, retry, dead) | ✅ done |
| P-14 | Digest-воркер (§3.2.5, §3.3) | P-02, P-13 | §4.4 | §3.2.5, §3.3 | `app/reconciler/digest.py` + миграция `004_dead_digest` | ✅ done |
| P-15 | Observability baseline (§3.2.6) | P-12 | §11.1, §11.4 | §3.2.6 | `app/observability/metrics.py`, structured JSON logger | ✅ done |
| P-16 | Grafana dashboards (§3.2.6) | P-15 | §11.1 | §3.2.6 | `grafana/provisioning/*.json` (4 дашборда) | ✅ done |
| P-17 | E2E + DoD (§3.4, §3.5) | все P-* | §3.4, §3.5 | §3.4, §3.5 | `tests/e2e/test_mvp_dod.py`, `scripts/run_dod_check.sh` | ✅ done |

### 1.2. TRIZ-gates MVP (ROADMAP §10.1) — все 6 внедрены ✅

| Принцип ТРИЗ | Gate | Задача | Статус |
|---|---|---|---|
| §11 (Заранее подложенная подушка) | `dead`-состояние в `search_outbox` | P-02 | ✅ done |
| §22 (Обратить вред в пользу) | Dead-digest-агрегатор hourly | P-14 | ✅ done |
| §16 (Частичное/избыточное действие) | Fast-path по `content_hash` | P-06, P-07 | ✅ done |
| §25 (Самообслуживание) | Reconciler сам находит работу | P-13 | ✅ done |
| §19 (Периодическое действие) | Reconciler работает по расписанию 30с | P-13 | ✅ done |
| §35 (Изменение параметров) | `content_hash` в схеме `documents` | P-01 | ✅ done |

### 1.3. DoD MVP (ROADMAP §3.4) — все 6 пунктов

- [x] DoD 1: `POST /index` создаёт документ в PG и (≤ 30 сек) — в Qdrant.
- [x] DoD 2: `POST /search` возвращает RRF-fused результаты, дедуплицированные по `doc_id`.
- [x] DoD 3: Reconciler восстанавливает рассинхрон при принудительном падении Qdrant.
- [x] DoD 4: Prometheus-метрики публикуются; Grafana-дашборд отображает latency, throughput, lag, dead-letter count.
- [x] DoD 5: `dead`-записи появляются в `search_outbox_dead_digest` (проверка инъекцией сломанного документа).
- [x] DoD 6: Fast-path срабатывает: повторная индексация того же контента не пересчитывает embedding.

---

## 2. Фаза Critical — production-safe baseline

> **ROADMAP §4** | **Промптов**: 15 (C-00..C-14) | **Статус**: ⏳ pending (промпты готовы в `CRITICAL-PROMPTS.md`, реализация не начата)

### 2.1. Задачи Critical

| ID | Компонент ROADMAP §4.2 | Зависимости | ARCHITECT §ref | ROADMAP §ref | Артефакт | Статус |
|----|---|---|---|---|---|---|
| C-00 | Phase bootstrap (§4 вводная) | все P-* | §17.2, §11.5 | §4 | расширение `app/config.py` (6 секций), eval-датасет baseline | ⏳ pending |
| C-01 | Cross-encoder reranker (§4.2.1) | C-00 | §7.3, §5.1 | §4.2.1 | `app/reranker/service.py` (bge-reranker-v2-m3 + батчинг) | ⏳ pending |
| C-02 | Weighted fusion (§4.2.2) | C-00 | §7.2 | §4.2.2 | `app/search/fusion.py::weighted_fuse` (min-max norm, α=0.5) | ⏳ pending |
| C-03 | Circuit breaker для reranker (§4.2.3) | C-01 | §6.6 | §4.2.3 | `app/reranker/circuit_breaker.py` (rolling window, half-open) | ⏳ pending |
| C-04 | Speculative rerank (§4.2.4) | C-01, C-05 | §6.5 | §4.2.4 | `app/search/speculative.py` (параллельный rerank на lex top-10) | ⏳ pending |
| C-05 | Search API integration | C-01..C-04 | §14.1, §6.1 | §4.2.1–4.2.4 | обновлённый `app/api/routes/search.py` + `SearchOrchestrator` | ⏳ pending |
| C-06 | Фасеты в PostgreSQL (§4.2.5) | C-00 | §8.4 | §4.2.5 | `app/search/facets.py` + поле `facets` в `SearchResponse` | ⏳ pending |
| C-07 | Оффлайн-eval nightly job (§4.2.6) | C-05, C-00 | §11.5 | §4.2.6 | `app/eval/nightly.py` + миграция `005_eval_datasets` + CI gate | ⏳ pending |
| C-08 | `wait_for_index` endpoint (§4.2.7) | P-07 | §14.3, §4.5 | §4.2.7 | `app/api/routes/index.py::index_status` | ⏳ pending |
| C-09 | Degraded mode lex-only (§4.2.8) | P-09, P-10 | §14.1, §4.6 | §4.2.8 | feature flag `vector_search_enabled`, `partial_result()` | ⏳ pending |
| C-10 | Push-down фильтры basic (§4.2.9) | P-09, P-10 | §8.2 | §4.2.9 | `app/search/pushdown.py` (selectivity по `reltuples`) | ⏳ pending |
| C-11 | Адаптивный throttle (§4.2.10) | P-07 | §4.6 | §4.2.10 | 202 Accepted при `pending_count > 50k`, watermark 100k → reindex stub | ⏳ pending |
| C-12 | Observability additions | C-03, C-10, C-07 | §11.1 | §4.5 | `app/observability/metrics.py` (+15 метрик) | ⏳ pending |
| C-13 | Grafana Critical dashboards | C-12 | §11.1 | §4.5 | `grafana/provisioning/dashboards/critical-*.json` (4 дашборда) | ⏳ pending |
| C-14 | E2E + DoD Critical | все C-* | §3.4, §3.5 (аналог) | §4.4, §4.5 | `tests/e2e/test_critical_dod.py`, `scripts/run_dod_check_critical.sh` | ⏳ pending |

### 2.2. TRIZ-gates Critical (ROADMAP §10.2) — 9 gate'ов, все pending

| Принцип ТРИЗ | Gate | Задача | Статус |
|---|---|---|---|
| §9 (Предварительное антидействие) | Circuit breaker для reranker | C-03 | ⏳ pending |
| §19 (Периодическое действие) | Circuit breaker с окном 60 сек | C-03 | ⏳ pending |
| §21 (Проскочить) | Speculative rerank | C-04 | ⏳ pending |
| §27 (Дешёвая недолговечность) | `wait_for_index_token` | C-08 | ⏳ pending |
| §16 (Частичное/избыточное действие) | `partial_result()` при `DeadlineExceeded` | C-09 | ⏳ pending |
| §9 (Предварительное антидействие) | `degraded` flag + feature-flag `vector_search_enabled` | C-09 | ⏳ pending |
| §23 (Обратная связь) | Nightly eval → regression → блокировка релиза | C-07 | ⏳ pending |
| §21 (Проскочить) | 202 Accepted при `pending_count > threshold` | C-11 | ⏳ pending |
| §17 (Переход в другое измерение) | Push-down фильтры (Postgres как первичный фильтр) | C-10 | ⏳ pending |

### 2.3. DoD Critical (ROADMAP §4.4) — 6 пунктов, все pending

- [ ] DoD 1: Cross-encoder поднимает nDCG@10 на 5–10% относительно RRF baseline на eval-датасете.
- [ ] DoD 2: Circuit breaker автоматически отключает rerank при инъекции искусственной деградации (chaos-сценарий в CI).
- [ ] DoD 3: Nightly eval ловит регрессии и блокирует релиз при превышении threshold.
- [ ] DoD 4: API остаётся доступным при принудительном падении Qdrant — возвращает lex-only результаты с `degraded: true`.
- [ ] DoD 5: Polling `/index/status/{token}` корректно сообщает `pending → done` после индексации.
- [ ] DoD 6: Push-down фильтры работают: на селективном фильтре (`category='ML' AND created_after > '2026-01-01'`) latency ≤ 50 мс vs 200 мс без push-down.

### 2.4. Жёсткие зависимости (ROADMAP §8.1)

```
Critical
├─ C-00 (bootstrap) ─────────────────►  все C-*
├─ C-01 (cross-encoder) ──────────────►  C-03 (circuit breaker), C-04 (speculative), C-05 (integration)
├─ C-02 (weighted fusion) ───────────►  C-05 (integration)
├─ C-03 (circuit breaker) ───────────►  C-05, C-12 (метрики)
├─ C-04 (speculative rerank) ────────►  C-05
├─ C-07 (nightly eval) ──────────────►  Production-Ready: regression gate для миграций
├─ C-10 (push-down basic) ───────────►  Production-Ready: selectivity-эвристика из tenant_search_stats
└─ C-06 (facets) ────────────────────►  Production-Ready: инкрементальные фасеты
```

---

## 3. Фаза Production-Ready — полноценная эксплуатация

> **ROADMAP §5** | **Задач**: 15 (PR-01..PR-15) | **Статус**: ⏳ pending (заблокировано задачами Critical)

### 3.1. Задачи Production-Ready

| ID | Компонент ROADMAP §5.2 | Зависимости | ARCHITECT §ref | ROADMAP §ref | Артефакт | Статус |
|----|---|---|---|---|---|---|
| PR-01 | Персонализация — `users` + `profile_vector` (§5.2.1) | C-14 (exit criteria) | §10.1, §10.2 | §5.2.1 | миграция `006_users`, `app/services/personalization.py`, EMA worker при клике | ⏳ pending |
| PR-02 | `search_events` — партиционирование + архив (§5.2.2) | PR-01 | §3.1, §11.4 | §5.2.2 | миграция для заполнения структуры MVP, daily partitions, архивация в S3 Parquet 90 дней | ⏳ pending |
| PR-03 | A/B-фреймворк (§5.2.3) | PR-02 | §11.3 | §5.2.3 | `app/services/ab_testing.py`, sticky distribution по `user_id`, sequential testing | ⏳ pending |
| PR-04 | MMR anti-bubble diversification (§5.2.4) | PR-01 | §10.5 | §5.2.4 | `app/search/diversify.py` (MMR, λ=0.7), `diversify=true` в API | ⏳ pending |
| PR-05 | Контекстная персонализация (§5.2.5) | PR-01 | §10.6 | §5.2.5 | `SearchContext {device, session_id, time_of_day}`, multiple profile vectors | ⏳ pending |
| PR-06 | Distributed tracing OpenTelemetry (§5.2.6) | C-12 | §11.2 | §5.2.6 | `app/observability/tracing.py`, OTel SDK, export в Tempo, trace↔log correlation | ⏳ pending |
| PR-07 | Миграционный фреймворк embedding-моделей (§5.2.7) | PR-02, C-07 | §13.1–13.4 | §5.2.7 | blue-green коллекции, backfill-воркер, канареечный reindex 1%, snapshot-on-reindex, rollback | ⏳ pending |
| PR-08 | Shadow traffic (§5.2.8) | PR-03 | §11.6 | §5.2.8 | `app/services/shadow_traffic.py`, параллельный расчёт новой стратегии, лог в `search_events.variant` | ⏳ pending |
| PR-09 | Chaos engineering weekly (§5.2.9) | PR-06, C-09 | §11.7 | §5.2.9 | `tests/chaos/*.py`, weekly cron в non-prod, сценарии kill Qdrant / delay reranker / fill Redis cache | ⏳ pending |
| PR-10 | Adaptive K по `tenant_search_stats` (§5.2.10) | PR-02 | §6.4 | §5.2.10 | миграция `tenant_search_stats`, adaptive K_lexical/K_vector по doc_count и avg_query_len | ⏳ pending |
| PR-11 | Инкрементальные фасеты (§5.2.11) | C-06 | §8.5 | §5.2.11 | Redis-кэш `facets:{query_hash}:{page}`, TTL 10 мин, incremental merge | ⏳ pending |
| PR-12 | Многоязычность enhanced (§5.2.12) | — | §9.4, §9.3 | §5.2.12 | `fasttext-langid`, смешанные запросы token-level, транслитерация `cyrtranslit` | ⏳ pending |
| PR-13 | Асимметричное индексирование (§5.2.13) | P-05 | §5.6 | §5.2.13 | chunking по 512 токенов overlap 50, взвешенное среднее секций (title × 3) | ⏳ pending |
| PR-14 | Schema drift контракт-тесты (§5.2.14) | P-04 | §15.4 | §5.2.14 | CI gate: payload Qdrant ↔ documents Postgres, единый `QdrantPayload` | ⏳ pending |
| PR-15 | Negative cache для embeddings (§5.2.15) | P-06 | §5.3 | §5.2.15 | Redis `null` cache TTL 60 сек для сломанных входов | ⏳ pending |

### 3.2. TRIZ-gates Production-Ready (ROADMAP §10.3) — 15 gate'ов, все pending

| Принцип ТРИЗ | Gate | Задача | Статус |
|---|---|---|---|
| §13 (Сделай наоборот) | MMR anti-bubble | PR-04 | ⏳ pending |
| §17 (Переход в другое измерение) | Контекстная персонализация (device/time/session) | PR-05 | ⏳ pending |
| §15 (Динамичность) | Adaptive `α` в EMA (0.5 для новых, 0.05 для established) | PR-01 | ⏳ pending |
| §22 (Обратить вред в пользу) | Shadow traffic | PR-08 | ⏳ pending |
| §9 (Предварительное антидействие) | Chaos engineering weekly | PR-09 | ⏳ pending |
| §10 (Предварительное действие) | Канареечный reindex (1% перед full) | PR-07 | ⏳ pending |
| §11 (Заранее подложенная подушка) | Snapshot-on-reindex | PR-07 | ⏳ pending |
| §26 (Копирование) | Blue-green коллекции | PR-07 | ⏳ pending |
| §17 (Переход в другое измерение) | Distributed tracing (metrics ↔ logs ↔ traces) | PR-06 | ⏳ pending |
| §23 (Обратная связь) | Adaptive K по `tenant_search_stats` | PR-10 | ⏳ pending |
| §3 (Местное качество) | Инкрементальные фасеты | PR-11 | ⏳ pending |
| §1 (Сегментация) | Смешанные запросы (token-level) | PR-12 | ⏳ pending |
| §3 (Местное качество) | Асимметричное индексирование | PR-13 | ⏳ pending |
| §35 (Изменение параметров) | NFC-нормализация | PR-12 | ⏳ pending |
| §16 (Частичное/избыточное действие) | Negative cache | PR-15 | ⏳ pending |

### 3.3. DoD Production-Ready (ROADMAP §5.4)

- [ ] `profile_vector` обновляется при каждом клике; для нового пользователя стартует с cold-start, через 10 кликов — стабилизируется.
- [ ] A/B-фреймворк корректно распределяет трафик sticky по `user_id`; метрики по вариантам считаются ежедневно.
- [ ] OpenTelemetry traces доступны в Tempo; `trace_id` из логов Loki открывает соответствующий trace.
- [ ] `search_events` партиционируется по дням; партиции старше 90 дней архивируются в S3 (Parquet) и `DROP`-аются без VACUUM.
- [ ] Смена embedding-модели — zero-downtime: канареечный reindex на 1% проходит threshold, full reindex за часы, переключение по feature-flag, rollback из snapshot за минуты.
- [ ] Shadow traffic работает: оба варианта логируются в `search_events` с `variant`, клиенту отдаётся только старый.
- [ ] Chaos-сценарии (kill Qdrant, delay reranker, fill Redis cache) проходят в CI, postcondition-чеки зелёные.
- [ ] Адаптивные `K` меняются в рантайме в зависимости от `tenant_search_stats`.
- [ ] Смешанные запросы (русский + английский) возвращают релевантные результаты из обоих языков.
- [ ] Длинные документы чанкованы и возвращаются в поиске по максимальному скору чанка.

### 3.4. Жёсткие зависимости (ROADMAP §8.1)

```
Production-Ready
├─ PR-02 (search_events) ────────────►  Improvements: LambdaMART (нужны кликовые данные)
├─ PR-03 (A/B framework) ────────────►  Improvements: multi-armed bandit для α
├─ PR-07 (migration framework) ──────►  Improvements: CDC readiness (outbox готов к смене потребителя)
├─ PR-10 (tenant_search_stats) ──────►  Improvements: tiering (нужна статистика по тенантам)
└─ PR-08 (shadow traffic) ──────────►  Improvements: канареечный rollout каскадного fusion
```

### 3.5. Изолированные компоненты (ROADMAP §8.3 — можно внедрять в любой момент)

- **PR-12** (Многоязычность enhanced) — частично изолирован (нужен только P-09 lexical).
- **PR-14** (Schema drift контракт-тесты) — изолирован, добавляется в CI в любой момент.
- **PR-15** (Negative cache) — изолирован, добавляется в `EmbeddingCache` в любой момент.
- **PR-04** (MMR diversification) — изолирован, может работать на pure fusion (без персонализации).

---

## 4. Фаза Improvements — масштабирование и оптимизации

> **ROADMAP §6** | **Задач**: 13 (IM-01..IM-13) | **Статус**: ⏳ trigger-based (запускаются по достижении триггеров, не по расписанию)

### 4.1. Задачи Improvements (с триггерами)

| ID | Компонент ROADMAP §6.2 | Триггер | Зависимости | ARCHITECT §ref | ROADMAP §ref | Артефакт | Статус |
|----|---|---|---|---|---|---|---|
| IM-01 | Learned ranking LambdaMART (§6.2.1) | ≥ 4 недели кликовых данных в `search_events` | PR-02 | §7.4 | §6.2.1 | `app/learning/lambdamart.py` (LightGBM/XGBoost pairwise), weekly переобучение, ONNX export | ⏳ optional |
| IM-02 | Каскадный выбор fusion (§6.2.2) | Cross-encoder стабилен; метрики `score[0]-score[5]` ≥ 1 недели | C-01, C-05 | §7.5 | §6.2.2 | `app/search/cascade.py` (RRF → conditional rerank → optional learned), `fusion='cascade'` | ⏳ optional |
| IM-03 | Тенантное tiering A/B/C (§6.2.3) | Появление ≥ 1 enterprise-тенанта или ≥ 3 free-тенантов | PR-10 | §12.6 | §6.2.3 | миграция `tenants` с `tier`, per-tier SLO/rate-limit | ⏳ optional |
| IM-04 | Cost-of-durability per tier (§6.2.4) | IM-03 активирован | IM-03 | §15.6 | §6.2.4 | `write_consistency=1` для Tier C, `=majority` для A/B | ⏳ optional |
| IM-05 | PostgreSQL scaling (§6.2.5) | `> 1M документов` или `> 500 RPS sustained` | — | §12.1 | §6.2.5 | 1 primary + 2 read replicas, PgBouncer transaction-mode pool=50, hash-партиции по `tenant_id` | ⏳ optional |
| IM-06 | Qdrant scaling (§6.2.6) | `> 10M векторов` в коллекции | — | §12.2 | §6.2.6 | пересоздание с `shard_number=8`, `replication_factor=2`, optional PQ quantization | ⏳ optional |
| IM-07 | API Layer autoscaling (§6.2.7) | Любой из scaling triggers | — | §12.3 | §6.2.7 | HPA по CPU 70%, rate-limiting Redis token bucket per tier | ⏳ optional |
| IM-08 | Embedding Worker autoscaling (§6.2.8) | `XLEN embeddings.queue > 10000` sustained | — | §12.4 | §6.2.8 | HPA по длине очереди Redis Streams, 4–8 параллельных воркеров на GPU-ноду | ⏳ optional |
| IM-09 | Redis Cluster (§6.2.9) | `> 10 GB` кэша или HA requirement | — | §12.5 | §6.2.9 | 3 master + 3 replica, 16 GB RAM, eviction `allkeys-lru` | ⏳ optional |
| IM-10 | Connection-aware routing (§6.2.10) | p99 на отдельной реплике систематически > 1.5× от средней | IM-05 | §12.7 | §6.2.10 | динамическая балансировка через `pg_stat_activity`, hysteresis 30с | ⏳ optional |
| IM-11 | CDC migration Debezium (§6.2.11) | `> 100M документов` или `> 10k RPS записи` | PR-07 | §15.5 | §6.2.11 | Debezium → Kafka → consumer, outbox остаётся safety net | ⏳ optional |
| IM-12 | Адаптивный `k` в RRF (§6.2.12) | Опционально, без жёсткого триггера | PR-10 | §7.1 | §6.2.12 | `k = 30 + 10 * log(doc_count)` для крупных коллекций | ⏳ optional |
| IM-13 | Multi-armed bandit для `α` (§6.2.13) | `≥ 1 месяц` A/B-данных по weighted fusion | PR-03, C-02 | §7.2 | §6.2.13 | Thompson sampling per-query, self-tuning α | ⏳ optional |

### 4.2. TRIZ-gates Improvements (ROADMAP §10.4) — 8 gate'ов, все optional

| Принцип ТРИЗ | Gate | Задача | Статус |
|---|---|---|---|
| §16 (Частичное/избыточное действие) | Каскадный fusion (rerank только к неуверенным запросам) | IM-02 | ⏳ optional |
| §13 (Сделай наоборот) | Каскад: стратегия по confidence результата, а не по запросу | IM-02 | ⏳ optional |
| §25 (Самообслуживание) | Confidence-порог определяется по историческим nDCG | IM-02 | ⏳ optional |
| §1 (Сегментация) | Tenant tiering (A/B/C) | IM-03 | ⏳ optional |
| §3 (Местное качество) | Per-tier SLO и resources | IM-03, IM-04 | ⏳ optional |
| §15 (Динамичность) | Connection-aware routing | IM-10 | ⏳ optional |
| §35 (Изменение параметров) | Per-tier `write_consistency` | IM-04 | ⏳ optional |
| §13 (Сделай наоборот) | CDC readiness: outbox готов к смене потребителя | IM-11 | ⏳ optional |

### 4.3. DoD Improvements (ROADMAP §6.4 — per-компонентно)

- [ ] **LambdaMART** (IM-01): обучена на ≥ 4 неделях данных; инференс < 5 мс; nDCG@10 +8–15% над RRF на eval-датасете; A/B подтверждает эффект на реальном трафике.
- [ ] **Каскадный fusion** (IM-02): доля запросов, дошедших до cross-encoder'а, ≤ 30% (vs. 100% при full-rerank); nDCG@10 не ниже full-rerank.
- [ ] **Tiering** (IM-03/04): Tier A/B/C имеют разные SLO и rate limits, зафиксированные в `tenants` таблице; метрики по tier'ам отдельны в Grafana.
- [ ] **PostgreSQL scaling** (IM-05): PgBouncer держит 500+ RPS/instance; read replicas работают (lag < 5 сек); hash-партиционирование включено при > 10M doc.
- [ ] **Qdrant scaling** (IM-06): `shard_number=8` при > 10M векторов; `replication_factor=2` (Tier A/B), `=1` (Tier C); scalar int8 активен.
- [ ] **Redis Cluster** (IM-09): 6 нод (3 master + 3 replica), HA подтверждена chaos-сценарием «убить master».
- [ ] **Connection-aware routing** (IM-10): p99 ≤ 80 мс для Tier A; распределение нагрузки между репликами равномерное (CV < 0.2).
- [ ] **CDC** (IM-11): trigger (>100M doc или >10k RPS) активирован; outbox-структура не изменилась; lag после миграции ≤ 2 секунд.

### 4.4. Мягкие зависимости (ROADMAP §8.2)

- **IM-12** (Adaptive `k` в RRF): можно внедрить в Production-Ready, но нужен `tenant_search_stats` — поэтому мягко зависит от PR-10.
- **IM-02** (Каскадный fusion): можно внедрить в Production-Ready, но без `learned` стадии 3 — каскад вырождается в RRF + опциональный cross-encoder.

---

## 5. Сводная матрица компонентов × фаз (ROADMAP §7 — полный охват)

> Проверка: каждая строка из ROADMAP §7 имеет хотя бы одну задачу в BACKLOG. Empty cells = не предусмотрено (или уже сделано ранее).

### 5.1. PostgreSQL

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| `documents` + индексы (tsv, trgm, GIN attrs/tags) | ✅ P-01 | — | — | hash-партиции по `tenant_id` | ✅ P-01, IM-05 |
| `search_outbox` (с `dead`-состоянием) | ✅ P-02 | — | digest-воркер hourly (✅ P-14) | — | ✅ P-02, P-14 |
| `embedding_models` (с `is_active`) | ✅ P-03 | — | blue-green миграции | — | ✅ P-03, PR-07 |
| `users` + `profile_vector` | — | — | ✅ | — | ✅ PR-01 |
| `search_events` (партиционирование по дням) | структура ✅ P-01 | — | ✅ + архив в S3 | — | ✅ P-01 (structure), PR-02 (partitions+archive) |
| `tenant_search_stats` | структура ✅ P-01 | — | ✅ + adaptive K | — | ✅ P-01 (structure), PR-10 (adaptive K) |
| `tenants` с tiers | — | — | — | ✅ | ✅ IM-03 |
| `eval_datasets` + `eval_results` | — | ✅ C-07 | — | — | ✅ C-07 |
| Read replicas + PgBouncer | — | — | — | ✅ | ✅ IM-05 |

### 5.2. Qdrant

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| Коллекция `documents` (1024 dim, Cosine) | ✅ P-04 | — | — | — | ✅ P-04 |
| Payload-индексы (tenant, lang, tags, attrs, model, time) | ✅ P-04 | — | — | — | ✅ P-04 |
| Scalar int8 quantization | ✅ P-04 | — | — | — | ✅ P-04 |
| `shard_number=4`, `replication_factor=2` | ✅ P-04 | — | — | `shard_number=8`, per-tier consistency | ✅ P-04, IM-04, IM-06 |
| Snapshots в S3 (snapshot-on-reindex) | — | — | ✅ | — | ✅ PR-07 |
| Blue-green коллекции | — | — | ✅ | — | ✅ PR-07 |

### 5.3. Embedding Service

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| `bge-m3` default | ✅ P-05 | — | — | — | ✅ P-05 |
| Content-hash cache + query cache + negative cache | ✅ (первые два) P-06 | — | ✅ negative | — | ✅ P-06, PR-15 |
| Батчинг по 32 | ✅ P-05 | — | — | — | ✅ P-05 |
| Асимметричное индексирование (chunking, взвешенное среднее) | — | — | ✅ | — | ✅ PR-13 |
| Fast-path по `content_hash` | ✅ P-06, P-07 | — | — | — | ✅ P-06, P-07 |
| Cross-encoder reranker (bge-reranker-v2-m3) | — | ✅ | — | — | ✅ C-01 |

### 5.4. API (FastAPI)

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| `POST /search` с RRF | ✅ P-11 | — | — | — | ✅ P-11 |
| `POST /index` с dual-write + outbox | ✅ P-07 | — | — | — | ✅ P-07 |
| `DELETE /index/{doc_id}` soft-delete | ✅ P-08 | — | — | — | ✅ P-08 |
| `GET /index/status/{token}` (read-your-writes) | — | ✅ | — | — | ✅ C-08 |
| `GET /health/live`, `/ready`, `/metrics` | ✅ P-12 | — | — | — | ✅ P-12 |
| Weighted fusion с `α` | — | ✅ | — | multi-armed bandit для `α` | ✅ C-02, IM-13 |
| Cross-encoder rerank | — | ✅ | — | — | ✅ C-01, C-05 |
| Speculative rerank | — | ✅ | — | — | ✅ C-04 |
| Каскадный fusion (`fusion='cascade'`) | — | — | — | ✅ | ✅ IM-02 |
| `degraded` flag + `partial_result()` | — | ✅ | — | — | ✅ C-09 |
| Adaptive `timeout_ms` | ✅ P-11 (поле в schema) | ✅ (использование в deadline) | — | — | ✅ P-11, C-05, C-09 |
| `SearchContext` (device/session/time) | — | — | ✅ | — | ✅ PR-05 |
| `diversify=true` (MMR) | — | — | ✅ | adaptive `λ` | ✅ PR-04 |
| `personalize=true` | — | — | ✅ | — | ✅ PR-01, PR-05 |
| Фасеты в response | — | ✅ | — | — | ✅ C-06 |
| `experiment_id` (A/B) | — | — | ✅ | — | ✅ PR-03 |
| 202 Accepted при throttle | — | ✅ | — | — | ✅ C-11 |

### 5.5. Reconciler / Background Workers

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| Basic reconciler (30с, exponential backoff, max 20 → dead) | ✅ P-13 | — | — | — | ✅ P-13 |
| Dead-digest-воркер hourly | ✅ P-14 | — | — | — | ✅ P-14 |
| Circuit breaker для reranker | — | ✅ | — | — | ✅ C-03 |
| Profile-vector worker (EMA при клике) | — | — | ✅ | adaptive `α` | ✅ PR-01 |
| Nightly eval job (Recall@10, nDCG@10) | — | ✅ | — | — | ✅ C-07 |
| Backfill-воркер для миграций | — | — | ✅ | — | ✅ PR-07 |
| Канареечный reindex + snapshot-on-reindex | — | — | ✅ | — | ✅ PR-07 |
| Shadow traffic | — | — | ✅ | — | ✅ PR-08 |
| Chaos engineering weekly | — | — | ✅ | — | ✅ PR-09 |
| LambdaMART обучение (weekly) + инференс | — | — | — | ✅ | ✅ IM-01 |
| CDC consumer (Debezium → Kafka) | — | — | — | ✅ | ✅ IM-11 |

### 5.6. Фильтры и фасеты

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| Payload-filter в Qdrant (pre-filter) | ✅ P-10 | — | — | — | ✅ P-10 |
| Push-down в Postgres (basic, по `reltuples`) | — | ✅ | — | — | ✅ C-10 |
| Push-down с `selectivity_estimate` из `tenant_search_stats` | — | — | ✅ | — | ✅ PR-10 (tenant_search_stats) — уточняется |
| Фасеты на top-N в Postgres | — | ✅ | — | — | ✅ C-06 |
| Инкрементальные фасеты (Redis-кэш) | — | — | ✅ | — | ✅ PR-11 |

### 5.7. Многоязычность

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| `langdetect` (basic) | ✅ P-07 | — | `fasttext-langid` | — | ✅ P-07, PR-12 |
| NFC-нормализация + lowercase | ✅ P-07 | — | — | — | ✅ P-07 |
| Смешанные запросы (token-level language detection) | — | — | ✅ | — | ✅ PR-12 |
| Транслитерация для лексического канала | — | — | ✅ | — | ✅ PR-12 |
| Cross-lingual через bge-m3 | ✅ P-05 (бесплатно) | — | — | — | ✅ P-05 |

### 5.8. Observability

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| Prometheus-метрики | ✅ P-15 | ✅ расширения C-12 | — | — | ✅ P-15, C-12 |
| Structured JSON-логи | ✅ P-15 | — | — | — | ✅ P-15 |
| Distributed tracing (OTel → Tempo) | — | — | ✅ | — | ✅ PR-06 |
| Trace ↔ log correlation (Loki) | — | — | ✅ | — | ✅ PR-06 |
| A/B-фреймворк (sticky distribution, sequential testing) | — | — | ✅ | — | ✅ PR-03 |
| `dead_letter_count`, `circuit_breaker_open`, `qdrant_pushdown_rate` метрики | ✅ baseline (P-15) | ✅ наполнение (C-12) | — | — | ✅ P-15, C-12 |
| Grafana dashboards MVP | ✅ P-16 | — | — | — | ✅ P-16 |
| Grafana dashboards Critical | — | ✅ C-13 | — | — | ✅ C-13 |
| Roadmap progress dashboard | — | — | — | ✅ (опционально) | ⏳ не выделен в отдельную задачу — часть C-13/PR-* observability расширений |

### 5.9. Scaling / HA

| Компонент | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| Single-instance deployment | ✅ P-00 | — | — | — | ✅ P-00 |
| Read replicas Postgres | — | — | — | ✅ | ✅ IM-05 |
| PgBouncer | — | — | — | ✅ | ✅ IM-05 |
| Qdrant cluster sharding | — | — | — | ✅ | ✅ IM-06 |
| Redis Cluster (3 master + 3 replica) | — | — | — | ✅ | ✅ IM-09 |
| API autoscaling (target 70% CPU) | — | — | — | ✅ | ✅ IM-07 |
| Embedding Worker autoscaling (by XLEN) | — | — | — | ✅ | ✅ IM-08 |
| Connection-aware routing | — | — | — | ✅ | ✅ IM-10 |
| Tenant tiering (A/B/C) | — | — | — | ✅ | ✅ IM-03 |
| Cost-of-durability trade-off per tier | — | — | — | ✅ | ✅ IM-04 |

### 5.10. API-контракты (ARCHITECT §14)

| Endpoint/поле | MVP | Critical | Production-Ready | Improvements | Задача в BACKLOG |
|---|---|---|---|---|---|
| `POST /search` базовый (RRF, top-20) | ✅ P-11 | — | — | — | ✅ P-11 |
| `POST /search` с `fusion="weighted"` | — | ✅ | — | — | ✅ C-02, C-05 |
| `POST /search` с `rerank=true` | — | ✅ | — | — | ✅ C-01, C-05 |
| `POST /search` с `diversify=true` | — | — | ✅ | — | ✅ PR-04 |
| `POST /search` с `personalize=true` | — | — | ✅ | — | ✅ PR-01, PR-05 |
| `POST /search` с `context={device, session, time}` | — | — | ✅ | — | ✅ PR-05 |
| `POST /search` с `experiment_id` (A/B) | — | — | ✅ | — | ✅ PR-03 |
| `POST /search` с `fusion="cascade"` | — | — | — | ✅ | ✅ IM-02 |
| `POST /search` с `timeout_ms` (deadline) | ✅ P-11 | ✅ (partial_result) | — | — | ✅ P-11, C-09 |
| `POST /search` с `facets=[...]` | — | ✅ | — | — | ✅ C-06 |
| `SearchResponse.degraded` flag | заглушка ✅ P-11 | ✅ наполнение | — | — | ✅ P-11, C-09 |
| `SearchResponse.partial` flag | — | ✅ | — | — | ✅ C-09 |
| `POST /index` базовый (201 Created) | ✅ P-07 | — | — | — | ✅ P-07 |
| `POST /index` с 202 Accepted при throttle | — | ✅ | — | — | ✅ C-11 |
| `DELETE /index/{doc_id}` (204) | ✅ P-08 | — | — | — | ✅ P-08 |
| `GET /index/status/{token}` (polling) | — | ✅ | — | — | ✅ C-08 |
| `GET /health/live`, `/ready`, `/metrics` | ✅ P-12 | — | — | — | ✅ P-12 |

### 5.11. Итоговая сверка покрытия

✅ **Все 56 компонентов из ROADMAP §7** (PostgreSQL 9 + Qdrant 6 + Embedding Service 6 + API 17 + Reconciler 11 + Filters/Facets 5 + Multilingual 5 + Observability 8 + Scaling/HA 10 — итого 77 строк, но с учётом дублирования по фазам уникальных компонентов 56) — **покрыты задачами в BACKLOG**.

| Подсистема | Кол-во компонентов | Покрыто | Задачи |
|---|---|---|---|
| PostgreSQL | 9 | ✅ 9/9 | P-01, P-02, P-03, P-14, C-07, C-11, PR-01, PR-02, PR-07, PR-10, IM-03, IM-05 |
| Qdrant | 6 | ✅ 6/6 | P-04, PR-07, IM-04, IM-06 |
| Embedding Service | 6 | ✅ 6/6 | P-05, P-06, P-07, PR-13, PR-15, C-01 |
| API (FastAPI) | 17 | ✅ 17/17 | P-07, P-08, P-11, P-12, C-02, C-05, C-06, C-08, C-09, C-11, PR-01, PR-03, PR-04, PR-05, IM-02, IM-13 |
| Reconciler / Workers | 11 | ✅ 11/11 | P-13, P-14, C-03, C-07, PR-01, PR-07, PR-08, PR-09, IM-01, IM-11 |
| Filters & Facets | 5 | ✅ 5/5 | P-10, C-06, C-10, PR-10, PR-11 |
| Многоязычность | 5 | ✅ 5/5 | P-05, P-07, PR-12 |
| Observability | 8 | ✅ 8/8 | P-15, P-16, C-12, C-13, PR-03, PR-06 |
| Scaling / HA | 10 | ✅ 10/10 | P-00, IM-03, IM-04, IM-05, IM-06, IM-07, IM-08, IM-09, IM-10, IM-11 |
| API-контракты (§14) | 17 | ✅ 17/17 | (покрыты задачами API подсистемы) |

---

## 6. Сводная карта рисков (ROADMAP §12 — extract)

| Риск | Фаза | Mitigation | Реализовано в |
|---|---|---|---|
| Окно несогласованности dual-write | MVP | Outbox + reconciler + `wait_for_index` polling | ✅ P-02, P-13, C-08 |
| Schema drift Postgres ↔ Qdrant | MVP | Единый `QdrantPayload`, контракт-тесты в CI | ✅ P-04, ⏳ PR-14 |
| Reranker деградирует под нагрузкой | Critical | Circuit breaker + speculative rerank + `timeout_ms` | ⏳ C-03, C-04, C-05 |
| Регрессия качества при смене модели | Critical | Nightly eval + блокировка релиза | ⏳ C-07 |
| Qdrant недоступен — поиск парализован | Critical | Degraded mode (lex-only) + `degraded` flag | ⏳ C-09 |
| High-cardinality tenant фильтры медленны | Critical | Push-down в Postgres | ⏳ C-10 |
| Outbox переполняется при длительном сбое | Critical | 202 Accepted throttle + full reindex по watermark | ⏳ C-11 |
| «Пузырь фильтров» от персонализации | PR | MMR diversification + adaptive `λ` | ⏳ PR-04 |
| Миграция embedding-модели даёт регрессию | PR | Канареечный reindex + snapshot + A/B | ⏳ PR-07 |
| GDPR / privacy для `profile_vector` | PR | Агрегат (не восстанавливает клики), retention 90 дней, GDPR-delete | ⏳ PR-01 |
| LambdaMART overfit на исторических данных | Improvements | Weekly переобучение, holdout, A/B перед rollout | ⏳ IM-01 |
| CDC усложняет операционную модель | Improvements | Миграция только по триггеру; outbox остаётся safety net | ⏳ IM-11 |
| Tier C `write_consistency=1` теряет данные | Improvements | Зафиксировано в SLA; переход на Tier B для критичных free | ⏳ IM-04 |
| Потеря задачи в Redis Streams | MVP | Reconciler подбирает по `next_retry_at <= now()` | ✅ P-13 |
| Дублирующая обработка (Reconciler + Worker) | MVP | `FOR UPDATE SKIP LOCKED` + идемпотентный upsert | ✅ P-13 |
| High `pending_count` в outbox | MVP / Critical | Adaptive throttle → full reindex по watermark | ⏳ C-11 |
| Qdrant потерян полностью | MVP / PR | Full reindex из Postgres; reconciler догоняет | ⏳ PR-07 |
| Redis cache miss avalanche | PR | Negative cache (60 сек TTL на `null`); staggered TTL | ⏳ PR-15 |
| Postgres bloat | Improvements | Регулярный `VACUUM ANALYZE`; мониторинг `pg_stat_user_tables`; hash-партиционирование | ⏳ IM-05 |
| Cold-start пользователя | PR | Pure fusion; history-aware boost; `α=0.5` для быстрого обучения | ⏳ PR-01 |
| Cross-lingual поиск ошибается на редких языках | PR | Fallback на лексический канал при `langdetect confidence < 0.7`; LaBSE как альтернатива | ⏳ PR-12 |
| Длинные документы теряют детали | PR | Асимметричное индексирование: chunking по 512 с overlap 50 | ⏳ PR-13 |
| Структурированные документы | PR | Взвешенное среднее: title × 3, abstract × 2, body × 1 | ⏳ PR-13 |
| Mixed-language запросы | PR | Token-level language detection; два параллельных ts-запроса через `tsquery &&` | ⏳ PR-12 |

---

## 7. Метрики прогресса внедрения (ROADMAP §13)

### 7.1. Метрики компонентов (публикуются в Grafana «Roadmap Progress»)

| Метрика | Цель | Текущее |
|---|---|---|
| `roadmap_components_total{phase="mvp"}` | 18 | 18 ✅ |
| `roadmap_components_total{phase="critical"}` | 10 | 0 ⏳ |
| `roadmap_components_total{phase="production_ready"}` | 15 | 0 ⏳ |
| `roadmap_components_total{phase="improvements"}` | 13 | 0 (trigger-based) ⏳ |
| `roadmap_components_done{phase="mvp"}` | 18 | 18 ✅ |
| `roadmap_components_done{phase="critical"}` | 10 | 0 ⏳ |
| `roadmap_triz_gates_done{phase="mvp"}` | 6 | 6 ✅ |
| `roadmap_triz_gates_done{phase="critical"}` | 9 | 0 ⏳ |
| `roadmap_triz_gates_done{phase="production_ready"}` | 15 | 0 ⏳ |
| `roadmap_triz_gates_done{phase="improvements"}` | 8 | 0 ⏳ |
| `roadmap_phase_status{phase="mvp"}` | done | done ✅ |
| `roadmap_phase_status{phase="critical"}` | in_progress | not_started ⏳ |

### 7.2. Критерии «фаза завершена» (ROADMAP §13.3)

Фаза считается завершённой, когда:
1. Все компоненты из §7 (соответствующая колонка) отмечены `✅`.
2. Все TRIZ-gates из §10 внедрены.
3. Все NFR из §11 достигнуты в течение ≥ 2 недель стабильно.
4. Chaos-сценарии для фазы (если есть) — зелёные.
5. DoD фазы выполнен.

**MVP**: ✅ все 5 критериев выполнены.
**Critical**: ⏳ 0/5 (требует C-00..C-14).
**Production-Ready**: ⏳ 0/5 (требует PR-01..PR-15).
**Improvements**: ⏳ trigger-based, per-компонентно.

---

## 8. Приоритеты и рекомендации

### 8.1. Что делать следующим (immediate next steps)

1. **Запустить C-00 (Phase bootstrap)** — расширить конфиг, подготовить eval-датасет, обновить `pyproject.toml`.
2. **Параллельно C-01 (Cross-encoder)** и **C-08 (wait_for_index)** — независимы, можно делать параллельно разными разработчиками.
3. **C-02 → C-03 → C-04 → C-05** — выстроить пайплайн качества (последовательно).
4. **Параллельно C-06, C-09, C-10, C-11** — изолированные расширения search/index.
5. **C-07 (Nightly eval)** — после C-05, нужна готовая стратегия fusion.
6. **C-12, C-13** — observability + Grafana.
7. **C-14** — финальная приёмка.

### 8.2. Что НЕ делать сейчас (scope guard)

Все задачи Production-Ready (PR-01..PR-15) и Improvements (IM-01..IM-13) — **заблокированы** до завершения Critical. Исключения (ROADMAP §8.3 — изолированные компоненты, можно в любой момент):

- **PR-14** (Schema drift контракт-тесты) — добавляется в CI в любой момент, не зависит от Critical.
- **PR-15** (Negative cache) — добавляется в `EmbeddingCache` в любой момент.
- **PR-12** (Многоязычность enhanced) — частично изолирован (нужен только P-09).
- **PR-04** (MMR diversification) — изолирован, может работать на pure fusion.

Если команда решает внедрить изолированный PR-* компонент параллельно с Critical — зафиксировать в `worklog.md` как «опережение графика» и убедиться, что это не нарушит DoD Critical.

### 8.3. Риски планирования

- **GPU-зависимость C-01**: если в dev-окружении нет GPU, все тесты идут в `mock_mode`. DoD 1 (nDCG +5–10%) **невозможно** проверить без GPU. Mitigation: добавить `@pytest.mark.gpu` маркер, тест skip в CI без CUDA, обязателен в staging/prod.
- **Eval-датасет (C-00)**: ≥ 100 размеченных запросов требуют ручной работы. Если нет аналитиков — сгенерировать синтетически (10 документов × 10 запросов), но это снижает quality сигнала.
- **Speculative rerank (C-04)**: экономия 80 мс на p99 — заявленная цель. Реальная экономия зависит от соотношения `lex_task` / `vec_task` latency. Если lex всегда медленнее vec (аномалия) — speculative не помогает, fallback на full rerank. Зафиксировать как known limitation.

---

## 9. Ссылки на источники

- **ARCHITECT.md** (`analitics/ARCHITECT.md`) — v2.0, TRIZ-applied, источник истины для архитектуры.
- **ROADMAP.md** (`analitics/ROADMAP.md`) — v1.0, источник фаз, NFR-таргетов, TRIZ-gates, exit criteria.
- **MVP-PROMPTS.md** (`analitics/MVP-PROMPTS.md`) — v1.0, промпты P-00..P-17.
- **CRITICAL-PROMPTS.md** (`download/CRITICAL-PROMPTS.md`) — v1.0, промпты C-00..C-14.
- **BACKLOG.md** (этот документ) — v1.0, сводный трекинг задач.

---

*Документ сопровождает `ARCHITECT.md` v2.0 и `ROADMAP.md` v1.0. При изменении архитектуры или добавлении новых фаз — обновить BACKLOG синхронно. При завершении каждой задачи — обновить статус в соответствующей таблице.*
