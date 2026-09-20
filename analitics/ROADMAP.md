# ROADMAP.md — Достижение архитектуры гибридного поиска (PostgreSQL + Qdrant)

> Версия документа: **1.0**
> Источник: `ARCHITECT.md` v2.0 (TRIZ-applied)
> Стек: Python (FastAPI), PostgreSQL 16, Qdrant 1.x, Redis 7, OpenTelemetry
> Статус: готов к реализации
> Методология: фазы отсортированы по 4 категориям зрелости — **MVP → Critical → Production-Ready → Improvements**

---

## Содержание

1. [Обзор и принципы категоризации](#1-обзор-и-принципы-категоризации)
2. [Сводная карта фаз](#2-сводная-карта-фаз)
3. [Фаза MVP — минимальный рабочий контур](#3-фаза-mvp--минимальный-рабочий-контур)
4. [Фаза Critical — production-safe baseline](#4-фаза-critical--production-safe-baseline)
5. [Фаза Production-Ready — полноценная эксплуатация](#5-фаза-production-ready--полноценная-эксплуатация)
6. [Фаза Improvements — масштабирование и оптимизации](#6-фаза-improvements--масштабирование-и-оптимизации)
7. [Сводная матрица фаз × компонентов](#7-сводная-матрица-фаз--компонентов)
8. [Карта зависимостей между фазами](#8-карта-зависимостей-между-фазами)
9. [Критерии перехода между фазами (exit criteria)](#9-критерии-перехода-между-фазами-exit-criteria)
10. [TRIZ-gates по фазам](#10-triz-gates-по-фазам)
11. [NFR-таргеты по фазам](#11-nfr-таргеты-по-фазам)
12. [Риски и mitigations](#12-риски-и-mitigations)
13. [Метрики прогресса внедрения](#13-метрики-прогресса-внедрения)

---

## 1. Обзор и принципы категоризации

### 1.1. Назначение документа

Данный roadmap — операционная декомпозиция архитектуры, описанной в `ARCHITECT.md`. Он не повторяет архитектурные детали, а переводит их в последовательность поставляемых инкрементов с явными Definition of Done, целевыми NFR на каждой стадии и критериями перехода между категориями. Документ предназначен для engineering-команды, product-owners и SRE — он даёт общий словарь для обсуждения «что у нас уже есть, что критично, а что можно отложить».

Каждая фаза спроектирована как **самостоятельно поставляемый продукт**: после завершения фазы система остаётся в эксплуатационно-корректном состоянии и может продолжать работать даже если следующая фаза откладывается. Это устраняет «частично готовые» релизы, где половина фич зависит от ещё не доставленной另一半.

### 1.2. Принципы разбиения на 4 категории

Категории соответствуют зрелости системы с точки зрения эксплуатационных рисков, а не календарной последовательности:

- **MVP** — минимальный набор компонентов, без которого система не решает основную задачу (гибридный поиск end-to-end). Здесь нет «nice-to-have» — только то, без чего гибридного поиска не существует.
- **Critical** — компоненты, которые превращают «работающий MVP» в «production-safe MVP»: safety nets, circuit breakers, оффлайн-eval, degraded mode. Без них выкатка в production несёт неприемлемые риски каскадных сбоев и регрессий качества.
- **Production-Ready** — компоненты, превращающие систему из «безопасной» в «полноценную»: персонализация, A/B-фреймворк, distributed tracing, миграционный фреймворк, shadow traffic, chaos engineering. Это уровень, на котором команда может управлять качеством поиска через данные, а не только через конфиги.
- **Improvements** — масштабирование и оптимизации, которые нужны при росте нагрузки/данных. Не являются «обязательным финалом» — включаются по триггерам (например, >10M документов или >10k RPS).

### 1.3. Отличия от исходного roadmap'а в ARCHITECT.md

В `ARCHITECT.md` §16 фазы описаны как **календарные этапы** (Phase 1–5) с длительностью 2–4 недели каждая. В данном документе фазы реорганизованы по **категориям зрелости**:

- Phase 1 (MVP) → **MVP**
- Phase 2 (Качество) → **Critical**
- Phase 3 (Персонализация) + Phase 4 (Зрелость) → **Production-Ready** (объединены, т.к. обе про «production-grade эксплуатацию»)
- Phase 5 (Масштабирование) + часть Phase 4 (LambdaMART) → **Improvements**

Дополнительно в каждую категорию включены TRIZ-gates из соответствующих разделов ARCHITECT.md, чтобы архитектурные решения, предусмотренные ТРИЗ-анализом, не потерялись при декомпозиции на фазы.

---

## 2. Сводная карта фаз

| Категория | Длительность (ориентир) | Главная цель | Что лежит в основе |
|---|---|---|---|
| **MVP** | 2–3 недели | Гибридный поиск end-to-end: lex+vec, RRF, dual-write+outbox | Postgres + Qdrant + FastAPI + Reconciler |
| **Critical** | 2 недели | Production-safe: rerank, circuit breaker, nightly eval, degraded mode | Cross-encoder + circuit breaker + offline eval |
| **Production-Ready** | 4–5 недель | Управление качеством через данные: A/B, персонализация, tracing, миграции, chaos | Personalization + OTel + migration framework + chaos |
| **Improvements** | По триггерам | Масштабирование: tiering, sharding, replicas, learned ranking, CDC | Horizontal scaling + LambdaMART + CDC readiness |

**Ключевая инвариантность:** каждая следующая категория опирается на компоненты предыдущей, но не модифицирует их контракт. Например, learned ranking (Improvements) использует `search_events` из Production-Ready, но не меняет схему таблицы. Это позволяет возвращаться к предыдущей категории для доработок без каскадной переделки.

---

## 3. Фаза MVP — минимальный рабочий контур

### 3.1. Цели фазы

Доставить end-to-end работающий гибридный поиск: клиент вызывает `POST /index`, документ появляется в `POST /search`; лексический канал (Postgres + `pg_trgm` + tsvectors) и векторный канал (Qdrant + `bge-m3`) отдают кандидатов, те сливаются через RRF и возвращаются клиенту. Синхронизация между Postgres и Qdrant — через dual-write с outbox-восстановлением, что даёт eventual consistency с SLO lag ≤ 30 секунд. Базовая observability (Prometheus + structured logs) достаточна для отладки, но не для управления качеством — это нормально для MVP.

Важный принцип MVP: **TRIZ-gate'ы встраиваются с первого дня**, а не «потом». Это включает `dead`-состояние в `search_outbox`, digest-агрегатор dead-записей и fast-path по `content_hash`. Эти элементы стоят недорого на старте, но их добавление в зрелую систему потребует миграции данных — поэтому их лучше заложить сразу.

### 3.2. Компоненты фазы

#### 3.2.1. PostgreSQL — схема данных

- Таблица `documents` (source of truth): `id`, `tenant_id`, `external_ref`, `title`, `content`, `language`, `tags`, `attributes JSONB`, `embedding_model`, `embedding_rev`, `content_hash`, `created_at`, `updated_at`, `deleted_at`. Unique constraint `(tenant_id, external_ref)`.
- Таблица `search_outbox`: `id BIGSERIAL`, `document_id`, `op IN ('upsert','delete')`, `status IN ('pending','in_progress','done','failed','dead')`, `attempts`, `last_error`, `next_retry_at`, `content_hash`, timestamps.
- Колонка `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', title || ' ' || content)) STORED`.
- Индексы: `documents_tsv_idx (GIN tsv)`, `documents_trgm_idx (GIN title gin_trgm_ops, content gin_trgm_ops)`, `documents_tenant_lang_idx (tenant_id, language) WHERE deleted_at IS NULL`, `documents_attrs_gin_idx (GIN attributes jsonb_path_ops)`, `documents_tags_gin_idx (GIN tags)`.
- Индексы outbox: `search_outbox_pending_idx (next_retry_at) WHERE status IN ('pending','failed')`, `search_outbox_doc_idx (document_id, status)`.
- Таблица `embedding_models`: `name PK`, `dimension`, `description`, `is_default`, `is_active`, `created_at`. Сразу с `is_active` для мягкого удаления моделей (готовит почву для миграций в Production-Ready).

#### 3.2.2. Qdrant — коллекция

- Коллекция `documents`: vectors size=1024, distance=Cosine, `shard_number=4`, `replication_factor=2`, `write_consistency=majority`, `on_disk_payload=true`.
- HNSW: `m=16`, `ef_construct=200`, `full_scan_threshold=10000`.
- Optimizers: `default_segment_number=4`, `indexing_threshold=20000`.
- Quantization: scalar int8, `quantile=0.99`, `always_ram=true` (закладывается сразу — retrospective изменение потребует reindex).
- Payload точки: `doc_id`, `tenant_id`, `language`, `tags`, `attributes`, `model_name`, `model_rev`, `created_at`, `content_hash`.
- Payload-индексы на полях: `tenant_id` (keyword), `language` (keyword), `tags` (keyword[]), `attributes.category` (keyword), `attributes.price` (float), `model_name`+`model_rev` (keyword), `created_at` (datetime).

#### 3.2.3. Embedding Service

- Модель по умолчанию: `BAAI/bge-m3` (1024-мерный, мультиязычный).
- Батчинг: 32 документа за раз. На GPU (A10g) ~800 embeddings/sec; на CPU ~15/sec — только для dev.
- Content-hash кэш (Redis): ключ `hash:{sha256(content)}:{model_name}` → бинарный вектор. TTL=30 дней.
- Query embedding кэш (Redis): ключ `qemb:{model_name}:{sha256(query)}` → вектор. TTL=1 час.
- Идемпотентность: повторная индексация того же контента (по `content_hash`) пропускает вычисление embedding и upsert в Qdrant (fast-path, экономит ~3 мс/операцию).

#### 3.2.4. API (FastAPI)

- `POST /search` — принимает `{query, tenant_id, user_id?, filters, top_k, fusion='rrf', explain, timeout_ms}`. Параллельно выполняет лексический запрос к Postgres (BM25/`pg_trgm` similarity + filters + лимит `K_lexical=50`) и векторный запрос к Qdrant (kNN + payload filter + лимит `K_vector=50`). Дедупликация по `doc_id`, RRF fusion (k=60), возврат top-20.
- `POST /index` — открывает PG-транзакцию, INSERT в `documents` + INSERT в `search_outbox` (атомарно), после коммита публикует задачу в Redis Streams (`embeddings.queue`). Возвращает `{doc_id, status, indexed_at}`.
- `DELETE /index/{doc_id}` — soft-delete в Postgres + outbox `op='delete'`.
- `GET /health/live` — процесс жив.
- `GET /health/ready` — Postgres reachable + Qdrant reachable + outbox lag < threshold.
- `GET /metrics` — Prometheus exposition.

#### 3.2.5. Reconciler-воркер

- Интервал: 30 секунд. Сканирует `search_outbox WHERE status IN ('pending','failed') AND next_retry_at <= now() ORDER BY next_retry_at LIMIT 500 FOR UPDATE SKIP LOCKED`.
- Для каждой записи: `status='in_progress'`, `attempts += 1`, вычисление embedding (или reuse из кэша), upsert в Qdrant, при успехе `status='done'`.
- При ошибке: `status='failed'`, `next_retry_at = now() + min(2^attempts * 10s, 1h)`, `last_error = ...`.
- При `attempts > 20`: `status='dead'`, алерт.
- Digest-воркер (отдельный, раз в час): агрегирует `dead`-записи в `search_outbox_dead_digest` по `(tenant_id, model_name, error_type)` — карта здоровья без ручного разбора 10k строк.

#### 3.2.6. Observability baseline

- Prometheus-метрики: `search_latency_ms` (histogram), `index_lag_seconds` (gauge), `qdrant_upsert_errors_total` (counter), `embedding_cache_hit_rate` (gauge), `dead_letter_count` (gauge).
- Structured JSON-логи с `request_id`, `tenant_id`, `trace_id` (заглушка для будущего OTel).
- Базовые Grafana-дашборды: throughput, latency, error rate, outbox lag.

### 3.3. TRIZ-gates фазы

| Gate | Раздел ARCHITECT.md | Что встроено с первого дня |
|---|---|---|
| `dead`-состояние в outbox | §3.1, §4.4 | Предотвращает вечные ретраи, забивающие SELECT |
| Digest-агрегатор dead-записей | §4.4 | Превращает dead-letter в диагностический инструмент |
| Fast-path по `content_hash` | §4.3 | Пропуск upsert при совпадении хэша — экономия на reindex |
| `is_active` в `embedding_models` | §3.1 | Готовит почву для zero-downtime миграций |
| Партиционирование `search_events` (заложить структуру) | §3.1 | Таблица создаётся, партиционирование включается в Production-Ready |

### 3.4. Definition of Done (DoD)

- `POST /index` создаёт документ в Postgres и (в течение SLO 30 сек) — в Qdrant.
- `POST /search` возвращает RRF-fused результаты из двух каналов; результаты дедуплицированы по `doc_id`.
- Reconciler восстанавливает рассинхрон при принудительном падении Qdrant (тест: kill Qdrant, поднять, убедиться что outbox разгребается).
- Prometheus-метрики публикуются; Grafana-дашборд отображает latency, throughput, lag, dead-letter count.
- `dead`-записи появляются в `search_outbox_dead_digest` (проверка инъекцией заведомо сломанного документа).
- Fast-path срабатывает: повторная индексация того же контента не пересчитывает embedding (проверка по логу и `attempts=0` в outbox).

### 3.5. Целевые NFR фазы

| Параметр | Целевое значение |
|---|---|
| Latency p99 `/search` (без rerank) | ≤ 200 мс |
| Throughput индексации | ≥ 500 doc/min (single worker) |
| Throughput поиска | ≥ 100 RPS на инстанс API |
| Согласованность Postgres↔Qdrant | eventual, SLO lag ≤ 30 с |
| SLA | 99% (planned downtime допустим) |
| Retention `search_events` | не применимо (таблица появится в Production-Ready) |

### 3.6. Риски фазы и их mitigations

- **Окно несогласованности dual-write**: между коммитом PG и upsert в Qdrant документ виден в lex-канале, но не в векторном. Mitigation: короткий интервал reconciler'а (30 сек) + SLO lag. Read-your-writes не гарантируется — будет добавлен в Critical через `/index/status/{token}`.
- **Потеря задачи в Redis Streams**: если Redis упал после коммита PG, но до `XADD`, задача «зависнет». Mitigation: reconciler подбирает по `next_retry_at <= now()` — safety net не зависит от Redis.
- **Schema drift между Postgres и Qdrant**: на MVP риск низкий (схема простая), но Mitigation — единый pydantic `QdrantPayload` в кодовой базе. Контракт-тесты добавляются в Production-Ready.

---

## 4. Фаза Critical — production-safe baseline

### 4.1. Цели фазы

Превратить «работающий MVP» в «безопасный MVP»: добавить reranker, safety nets и оффлайн-eval. После этой фазы команда может выкатывать изменения в production без неприемлемого риска каскадных сбоев или необнаруженной регрессии качества. Главная идея — каждое «опасное» расширение (cross-encoder, weighted fusion) приходит вместе со своим «амортизатором» (circuit breaker, nightly eval, degraded mode).

Critical не добавляет нового функционала с точки зрения пользователя — пользователь по-прежнему получает список результатов. Но качество этих результатов значительно выше (nDCG@10 +5–10% от cross-encoder), а система устойчива к деградации reranker'а, падению Qdrant и регрессиям модели.

### 4.2. Компоненты фазы

#### 4.2.1. Cross-encoder reranker

- Модель: `BAAI/bge-reranker-v2-m3` на GPU-ноде.
- Пайплайн: top-50 после RRF → загрузка текстов (или первых 512 токенов) → батч пар `(query, doc_text)` → cross-encoder → сортировка по новому скору.
- Latency target: p99 ≤ 350 мс (включая rerank).
- Активация: опциональная через `rerank=true` в запросе.
- Выключение: через circuit breaker (см. §4.2.3).

#### 4.2.2. Weighted fusion

- Формула: `weighted_score(d) = α * norm(BM25(d)) + (1 - α) * norm(cosine(d))`, где `norm` — min-max нормализация по выборке.
- Default `α = 0.5`. Тюнинг — оффлайн на размеченной выборке (nDCG@10), либо через multi-armed bandit (в Improvements).
- Включается через `fusion='weighted'` в запросе.

#### 4.2.3. Circuit breaker для reranker

- Если за минуту `error_rate > 5%` или `latency_p95 > 500 мс` — автоматическое отключение rerank на 60 секунд.
- На время отключения: API отдаёт fusion-only результаты с `degraded: true` в ответе.
- Переоткрытие: через 60 секунд автоматически, при повторной деградации — снова закрывается.

#### 4.2.4. Speculative rerank

- При `rerank=true` cross-encoder запускается **параллельно** с запросом в Qdrant, используя **лексический top-10** как «спекулятивный вход».
- К моменту, когда fusion готов, у нас уже есть первые rerank-скоры для 10 документов; мы дозапускаем rerank только для новых документов из векторного топа.
- Экономит ~80 мс на p99.

#### 4.2.5. Фасеты в PostgreSQL

- Фасеты считаются на **отфильтрованном множестве top-N** (по fusion-score), а не по всей коллекции.
- SQL: `SELECT attributes->>'category' AS facet, count(*) FROM documents WHERE id = ANY($1) AND deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 20`.
- Возвращается в `SearchResponse.facets`.

#### 4.2.6. Оффлайн-eval (nightly job)

- Каждый день в non-prod запускается job:
  1. Загружает eval-датасет (размеченные запросы → релевантные документы).
  2. Прогоняет все доступные fusion-стратегии (RRF, Weighted, +Rerank).
  3. Считает Recall@10, nDCG@10, MRR.
  4. Сравнивает с baseline (вчера) и алертит при регрессии > 2% recall / 1% nDCG.
- Блокировка релиза при регрессии (CI gate).

#### 4.2.7. `wait_for_index` endpoint

- `GET /index/status/{token}` — polling для read-your-writes: клиент узнаёт, готов ли документ к поиску.
- Возвращает `{token, status}` где `status ∈ {pending, done, dead}`.
- Таймаут на стороне клиента: 5 секунд (если за 5 сек не `done` — клиент ретраит или сдаётся).

#### 4.2.8. Degraded mode (lex-only fallback)

- Feature flag `vector_search_enabled` (default `true`).
- При недоступности Qdrant (health-check падает) — автоматическое переключение на lex-only режим.
- В ответе поле `degraded: true` сигнализирует клиенту, что работал fallback.
- При `DeadlineExceeded` (timeout_ms превышен) — `partial_result()`: отдаём то, что успели посчитать, лучше 5 результатов за 100 мс, чем 0 за 200 мс.

#### 4.2.9. Push-down фильтры (basic)

- Если фильтр высокоселективен (`selectivity_estimate < 0.1` по `pg_class.reltuples`) — сначала `SELECT id FROM documents WHERE filters LIMIT 5000` в Postgres, затем передаём список в Qdrant как `MatchAny` на `doc_id`.
- Обходит HNSW и фильтрует векторным ранжированием только по кандидатурам — 3–5× ускорение на селективных фильтрах.
- Эвристика выбора — простая (по `reltuples`); уточнится в Production-Ready через `tenant_search_stats`.

#### 4.2.10. Адаптивный throttle при переполнении outbox

- При `pending_count > 50k` Index API автоматически переходит в режим `index_throttle=true`.
- Принимает документы, но отвечает `202 Accepted` вместо `201 Created`, сигнализируя клиенту о задержке индексации.
- При `pending_count > 100k` — запускается full reindex батчами по `model_rev` (см. Production-Ready §5.2.7).

### 4.3. TRIZ-gates фазы

| Gate | Раздел ARCHITECT.md | Эффект |
|---|---|---|
| Circuit breaker для reranker | §6.6 | Предотвращает каскадный таймаут при деградации reranker |
| Speculative rerank | §6.5 | Экономит ~80 мс на p99 |
| `wait_for_index_token` | §14.3 | Дешёвый короткоживущий токен для polling (вместо long-polling/webhook) |
| `degraded` flag + `partial_result()` | §14.1 | Заранее подложенный сигнал клиенту + graceful degradation |
| Nightly eval как regression detection | §11.5 | Замкнутый контур: регрессия → блокировка релиза |
| 202 Accepted при throttle | §4.6 | «Проскакиваем» этап подтверждения, не теряя документ |
| Push-down фильтры | §8.2 | 3–5× ускорение на селективных фильтрах |

### 4.4. Definition of Done (DoD)

- Cross-encoder поднимает nDCG@10 на 5–10% относительно RRF baseline на eval-датасете.
- Circuit breaker автоматически отключает rerank при инъекции искусственной деградации (chaos-сценарий в CI).
- Nightly eval ловит регрессии и блокирует релиз при превышении threshold.
- API остаётся доступным при принудительном падении Qdrant — возвращает lex-only результаты с `degraded: true`.
- Polling `/index/status/{token}` корректно сообщает `pending → done` после индексации.
- Push-down фильтры работают: на селективном фильтре (`category='ML' AND created_after > '2026-01-01'`) latency ≤ 50 мс vs. 200 мс без push-down.

### 4.5. Целевые NFR фазы

| Параметр | Целевое значение |
|---|---|
| Latency p99 `/search` (без rerank) | ≤ 150 мс |
| Latency p99 `/search` (с cross-encoder rerank top-50) | ≤ 350 мс |
| Throughput индексации | ≥ 1000 doc/min |
| Throughput поиска | ≥ 200 RPS на инстанс API |
| Recall@10 | не падает > 2% относительно baseline (nightly eval) |
| nDCG@10 | не падает > 1% относительно baseline |
| `embedding_cache_hit_rate` | ≥ 50% (стационар) |
| SLA | 99.5% |
| Lag | ≤ 10 секунд |

### 4.6. Риски фазы и их mitigations

- **Reranker деградирует под нагрузкой**: latency растёт, поисковые запросы таймаутятся. Mitigation: circuit breaker (§4.2.3) + speculative rerank (§4.2.4) + `timeout_ms` в запросе.
- **Регрессия качества при смене модели/стратегии**: не всегда видна на unit-тестах. Mitigation: nightly eval с порогами + блокировка релиза.
- **High-cardinality tenant фильтры в Qdrant**: payload-фильтр на `tenant_id` при больших коллекциях может быть медленным. Mitigation: push-down в Postgres (§4.2.9) — на селективных фильтрах Qdrant получает уже сокращённый список кандидатов.
- **Cross-encoder требует GPU**: при отсутствии GPU — недопустимая latency. Mitigation: явное документирование в SLA; для dev-окружения допустима mock-реализация cross-encoder'а, возвращающая RRF-скор.

---

## 5. Фаза Production-Ready — полноценная эксплуатация

### 5.1. Цели фазы

Превратить «безопасный MVP» в «полноценную production-систему»: команда получает инструменты для управления качеством поиска через данные (A/B-фреймворк, персонализация, оффлайн-eval), полную observability (distributed tracing), процедуры миграции embedding-моделей без downtime, и подтверждённую через chaos engineering устойчивость к реальным сбоям.

После этой фазы система способна:
- Управлять качеством: A/B-тесты между fusion-стратегиями, персонализация по профилю пользователя, оффлайн-eval как regression gate.
- Эволюционировать: zero-downtime миграция embedding-моделей (blue-green + канарейка + snapshot).
- Восстанавливаться: при потере Qdrant — full reindex за часы, не дни; при ошибке в новой модели — rollback из snapshot за минуты.
- Доказывать устойчивость: chaos-сценарии в CI, shadow traffic для новых стратегий.

### 5.2. Компоненты фазы

#### 5.2.1. Персонализация — user profile vector

- Таблица `users`: `id`, `tenant_id`, `profile_vector VECTOR(1024)`, `profile_version`, `created_at`.
- Воркер обновления `profile_vector` при каждом клике: `profile = α * embedding(clicked_doc) + (1-α) * profile`, `α=0.1` (медленная адаптация). Для новых пользователей `α=0.5` (быстрое обучение).
- Re-ranking: `final_score = 0.7 * fused_score + 0.3 * cosine(user_profile, doc_vector)`. Коэффициенты — гиперпараметры, тюнятся по CTR.
- Для cold-start пользователей персонализация отключается, используется pure fusion.
- History-aware boost (fallback при отсутствии `profile_vector`): `ORDER BY CASE WHEN category = ANY($recent_categories) THEN 1.0 ELSE 0.0 END DESC, fused_score DESC`.

#### 5.2.2. `search_events` — логирование запросов

- Таблица партиционируется по `created_at` (RANGE, daily partitions).
- Поля: `user_id`, `tenant_id`, `query_text`, `query_lang`, `top_doc_ids UUID[]`, `clicked_doc_id`, `position`, `fusion_strategy`, `experiment_id`, `latency_ms`, `created_at`.
- Индексы: `search_events_user_time_idx (user_id, created_at DESC)`, `search_events_exp_idx (experiment_id, created_at DESC)`.
- Партиции старше 90 дней архивируются в S3 (Parquet) и удаляются (`DROP PARTITION`).
- Используется как для A/B-анализа, так и для обучения LambdaMART (в Improvements).

#### 5.2.3. A/B-фреймворк

- `experiment_id` в каждом запросе. Распределение по вариантам — sticky по `user_id` (консистентное хеширование, чтобы один пользователь всегда видел один вариант).
- Метрики эксперимента (ежедневно): CTR, MRR, доля «no-click», latency p95.
- Statistical significance — sequential testing (раньше останавливается, чем fixed-horizon, без inflation of false positives).

#### 5.2.4. MMR anti-bubble diversification

- Чистая персонализация ведёт к «пузырю» — пользователь видит только то, что уже знает.
- MMR (Maximal Marginal Relevance) на финальном шаге: `MMR(d) = λ * relevance(d, q) - (1-λ) * max_{d' ∈ S} similarity(d, d')`, где `S` — уже отобранные документы, `λ=0.7` default.
- Гарантирует, что в top-10 будет хотя бы 2–3 документа из других категорий, чем последние клики пользователя.
- Активируется через `diversify=true` в запросе.
- Адаптивный `λ`: при падении `diversity_score` топ-10 система автоматически увеличивает `1-λ`.

#### 5.2.5. Контекстная персонализация

- `SearchContext {device, session_id, time_of_day}` в запросе.
- `profile_vector` может быть разный для разных сессий:
  - **Time-of-day profile**: morning (новости) vs evening (развлекательный контент).
  - **Device profile**: desktop (рабочие запросы) vs mobile (быстрые/развлекательные).
  - **Session profile**: короткоживущий профиль по последним N кликам в текущей сессии (`α=0.5`).
- Профиль для запроса выбирается по контексту.

#### 5.2.6. Distributed tracing (OpenTelemetry)

- Инструментация всех ключевых вызовов через OpenTelemetry Python SDK.
- Span `search.request` с атрибутами: `query_lang`, `fusion`, `experiment_id`, `user_id_hashed`.
- Дочерние spans: `pg.lexical`, `qdrant.vector`, `redis.cache_lookup`, `rerank`, `personalize`, `pg.facets`.
- Export в Tempo через OTel Collector.
- Логи в Loki коррелируются с traces по `trace_id` — три измерения observability (metrics ↔ logs ↔ traces).

#### 5.2.7. Миграционный фреймворк (смена embedding-модели)

- Сценарий zero-downtime миграции:
  1. Заводим запись в `embedding_models` с новой моделью и `is_default=false`. Создаём в Qdrant новую коллекцию `documents_v2`.
  2. Backfill-воркер: идёт по `documents` батчами по 1000, вычисляет новые embeddings, upsert в `documents_v2`. Скорость ~50k doc/hour на GPU-ноду.
  3. **Канареечный reindex**: перед full — на 1% случайных документов. Если средняя cosine similarity старой и новой модели < 0.6 — слишком сильное изменение семантики, нужна ручная проверка. Если > 0.95 — модель слишком похожа, возможно, не стоит тратить ресурсы.
  4. **Snapshot-on-reindex**: перед любым reindex — snapshot Qdrant в S3 (retention 30 дней). Если новый индекс даёт регрессию — rollback к snapshot за минуты, не часы.
  5. Переключение: `is_default=true` для новой модели. API начинает dual-write в обе коллекции. Search Orchestrator переключается по feature-flag.
  6. Smoke-тесты и A/B на 10% трафика 1–3 дня.
  7. После недели stable — удаляем старую коллекцию, снимаем dual-write.
- Сценарий полного восстановления после потери Qdrant:
  1. Поднимаем пустой Qdrant.
  2. Создаём коллекцию с текущей конфигурацией.
  3. Full reindex из Postgres по `documents` с фильтром `deleted_at IS NULL`.
  4. Reconciler накатывает то, что успело протечь после старта reindex.
  5. Включаем векторный канал через feature-flag.
- Оценка: 1M документов — ~20 часов на одну GPU-ноду, ~3 часа на 7 нод. На время восстановления поиск работает только по лексическому каналу.

#### 5.2.8. Shadow traffic

- При выкатке новой стратегии (например, переход с RRF на каскад) — не сразу переключаем трафик.
- Запускаем новую стратегию **параллельно** в shadow-режиме: на каждый запрос считаем оба результата, логируем оба в `search_events` (с пометкой `variant`), но клиенту отдаём только старый.
- Двойная нагрузка обращена в пользу: безопасная валидация без риска.
- Онлайн-метрики качества — через interleaved клики на следующей неделе.

#### 5.2.9. Chaos engineering (weekly)

- Каждое воскресенье 06:00 UTC в non-prod: плановые инъекции сбоев.
- Сценарии:
  - Убить Qdrant на 60 секунд → проверить, что API перешёл в lex-only без 5xx.
  - Задержать Reranker на 1 секунду → проверить, что circuit breaker открылся.
  - Забить Redis-кэш embeddings → проверить hit rate degradation graceful.
- Каждый сценарий имеет postcondition-чек в CI; провал блокирует релиз.

#### 5.2.10. Adaptive K (по `tenant_search_stats`)

- Таблица `tenant_search_stats`: `tenant_id PK`, `doc_count`, `avg_query_len`, `last_updated`.
- Система адаптирует `K_lexical`/`K_vector` под тенанта:
  - `< 10k` документов: `K_lexical = K_vector = 30`.
  - `> 1M`: `K = 100`.
  - `avg_query_len < 5` (короткие интенты): повышаем `K_lexical`, понижаем `K_vector`.
  - `avg_query_len > 20` (длинные запросы): наоборот.

#### 5.2.11. Инкрементальные фасеты

- При бесконечном скролле (load more) — не гонять полный SQL на каждое «show more».
- Redis-кэш `facets:{query_hash}:{page}` (TTL 10 минут) хранит предыдущий результат; инкрементально добавляем новые документы.

#### 5.2.12. Многоязычность enhanced

- `fasttext-langid` вместо `langdetect` (быстрее и точнее). Fallback на язык tenant при `confidence < 0.7`.
- Смешанные запросы: «купить iPhone 15 pro в Москве» — детектируем язык по токенам. Латиница → latin sub-query, кириллица → cyrillic sub-query, цифры/бренды → neutral, идут в оба канала. Для векторного канала — исходный запрос целиком (bge-m3 сам разбирает смешанность). Для лексического — два параллельных ts-запроса, объединяемых через `tsquery && tsquery`. Повышает recall на смешанных интентах на 6–8%.
- Транслитерация запроса (для лексического канала) — `cyrtranslit`.
- Unicode NFC-нормализация + нижний регистр — снимает класс ложных несовпадений (композитные vs декомпозированные символы).

#### 5.2.13. Асимметричное индексирование

- Короткие документы (< 200 токенов): один embedding на весь документ.
- Длинные документы (> 512 токенов): chunking по 512 токенов с overlap 50, индексируем несколько точек в Qdrant с общим `doc_id` и полем `chunk_idx`. На этапе поиска агрегируем через `max(score)` по `doc_id`.
- Структурированные документы (с известными секциями): взвешенное среднее embeddings секций (title × 3, abstract × 2, body × 1).

#### 5.2.14. Schema drift контракт-тесты

- Единый source of truth — pydantic-схема `QdrantPayload` в кодовой базе.
- При изменении схемы — миграция и в Postgres, и в Qdrant.
- Контракт-тесты в CI: payload точки Qdrant соответствует схеме `documents` Postgres.

#### 5.2.15. Negative cache для embeddings

- Для запросов, на которые модель вернула пустой/невалидный результат — кэшируем `null` на 60 секунд.
- Предотвращает повторные дорогие запросы к модели на заведомо сломанных входах.

### 5.3. TRIZ-gates фазы

| Gate | Раздел ARCHITECT.md | Эффект |
|---|---|---|
| MMR anti-bubble | §10.5 | Компенсирует «пузырь фильтров» от персонализации |
| Контекстная персонализация | §10.6 | Профиль выбирается по device/time/session |
| Adaptive α в EMA | §10.1 | Для новых пользователей `α=0.5`, для established `α=0.05` |
| Shadow traffic | §11.6 | Безопасная валидация новых стратегий на реальном трафике |
| Chaos engineering weekly | §11.7 | Плановые сбои выявляют слабые места до real incident |
| Канареечный reindex (semantic-diff) | §13.5 | Предварительная проверка на 1% перед full reindex |
| Snapshot-on-reindex | §13.6 | Откат за минуты, не часы |
| Distributed tracing (3 измерения) | §11.2 | metrics ↔ logs ↔ traces коррелируют по `trace_id` |
| Adaptive K | §6.4 | Автоматический баланс latency/recall под тенанта |
| Push-down с selectivity-эвристикой | §8.2 | 3–5× ускорение на селективных фильтрах |
| Инкрементальные фасеты | §8.5 | Local update вместо global recompute |
| Смешанные запросы | §9.4 | +6–8% recall на смешанных интентах |
| Асимметричное индексирование | §5.6 | Разная стратегия под разный контент |
| Schema drift контракт-тесты | §15.4 | Единый source of truth + CI gate |
| Negative cache | §5.3 | Предотвращает долбление модели на сломанных входах |
| Партиционирование `search_events` по дням | §3.1 | `DROP PARTITION` вместо дорогого DELETE |

### 5.4. Definition of Done (DoD)

- `profile_vector` обновляется при каждом клике; для нового пользователя стартует с cold-start, через 10 кликов — стабилизируется.
- A/B-фреймворк корректно распределяет трафик sticky по `user_id`; метрики по вариантам считаются ежедневно.
- OpenTelemetry traces доступны в Tempo; `trace_id` из логов Loki открывает соответствующий trace.
- `search_events` партиционируется по дням; партиции старше 90 дней архивируются в S3 (Parquet) и `DROP`-аются без VACUUM.
- Смена embedding-модели — zero-downtime: канареечный reindex на 1% проходит threshold, full reindex за часы, переключение по feature-flag, rollback из snapshot за минуты.
- Shadow traffic работает: оба варианта логируются в `search_events` с `variant`, клиенту отдаётся только старый.
- Chaos-сценарии (kill Qdrant, delay reranker, fill Redis cache) проходят в CI, postcondition-чеки зелёные.
- Адаптивные `K` меняются в рантайме в зависимости от `tenant_search_stats`.
- Смешанные запросы (русский + английский) возвращают релевантные результаты из обоих языков.
- Длинные документы чанкованы и возвращаются в поиске по максимальному скору чанка.

### 5.5. Целевые NFR фазы

| Параметр | Целевое значение |
|---|---|
| Latency p99 `/search` (без rerank) | ≤ 120 мс (достигнут архитектурный таргет) |
| Latency p99 `/search` (с rerank) | ≤ 350 мс |
| Throughput индексации | ≥ 2000 doc/min |
| Throughput поиска | ≥ 500 RPS на инстанс API |
| Доступность (SLA) | 99.9% |
| Lag | ≤ 5 секунд |
| `embedding_cache_hit_rate` | ≥ 60% (стационар) |
| Recall@10 (vs baseline) | не падает > 2% |
| nDCG@10 (vs baseline) | не падает > 1% |

### 5.6. Риски фазы и их mitigations

- **GDPR / privacy**: `profile_vector` — агрегат, не позволяет восстановить конкретные клики. `search_events` хранятся 90 дней, затем агрегируются в `user_stats` и удаляются. Пользователь может запросить экспорт и удаление данных (GDPR-style) — `profile_vector` обнуляется, профиль восстанавливается с cold-start.
- **Миграция embedding-модели даёт регрессию**: Mitigation — канареечный reindex + snapshot-on-reindex + A/B на 10% трафика + rollback из snapshot.
- **Schema drift между Postgres и Qdrant** на фазе активной эволюции схемы: Mitigation — контракт-тесты в CI; единый pydantic `QdrantPayload`; при изменении схемы — обязательная миграция в обоих сторонах.
- **Партиционирование `search_events` требует операций DBA**: Mitigation — автоматизация через pg_partman или кастомный скрипт, проверенный в chaos-сценарии «удалить партицию в пиковый час».
- **Cross-lingual поиск через векторный канал — может ошибаться на редких языках**: Mitigation — fallback на лексический канал при `langdetect confidence < 0.7`; для редких языков可以考虑 LaBSE (альтернативная модель в `embedding_models`).

---

## 6. Фаза Improvements — масштабирование и оптимизации

### 6.1. Цели фазы

Категория Improvements не имеет фиксированного срока — компоненты включаются по триггерам. Главная идея: при росте нагрузки или данных базовая архитектура (Production-Ready) начинает упираться в ресурсы (CPU, RAM, latency), и нужны структурные оптимизации. До достижения триггеров внедрение этих компонентов — лишняя сложность.

Категория делится на триггерные группы:

- **Триггер `> 1M документов` или `> 500 RPS sustained`**: PostgreSQL read replicas + PgBouncer, Qdrant sharding 4→8, Redis Cluster.
- **Триггер `> 100M документов` или `> 10k RPS записи`**: CDC (Debezium) вместо dual-write.
- **Триггер `> 4 недели кликовых данных`**: Learned ranking (LambdaMART).
- **Триггер `появление enterprise-тенантов`**: Tenant tiering (A/B/C) + connection-aware routing.
- **Без триггера (опционально)**: Каскадный fusion, адаптивный `k` в RRF, multi-armed bandit для `α`.

### 6.2. Компоненты фазы

#### 6.2.1. Learned ranking (LambdaMART)

- Когда накапливается достаточно кликовых данных (≥ 4 недели в `search_events`) — обучаем модель ранжирования.
- Группы фич:
  - **Lexical**: BM25 score, tf-idf title, tf-idf content, query length, exact-match title.
  - **Vector**: cosine score, rank in vector top-K.
  - **Fusion**: RRF score, weighted score.
  - **Document**: recency, length, pagerank-like authority, CTR в похожих запросах.
  - **User**: cosine(user_profile, doc_vector), исторический CTR user×category.
  - **Context**: device, geo, time_of_day.
- Модель: LightGBM или XGBoost в режиме LambdaMART (pairwise ranking). ~50–200 деревьев, глубина 6.
- Обучение: оффлайн, раз в неделю, на датасете `(query, shown_docs, clicked_doc)` → формируем пары «clicked > not-clicked».
- Деплой: модель экспортируется в ONNX или нативный pickle, загружается в Search Orchestrator.
- Инференс: ~1–5 мс, не требует GPU.
- Эффект: +8–15% nDCG над RRF.

#### 6.2.2. Каскадный выбор fusion

- Вместо статического выбора стратегии через `fusion` поле — **каскад**:
  1. **Стадия 1 (всегда)**: RRF на топ-50.
  2. **Стадия 2 (если RRF confidence низкий)**: confidence = `score[0] - score[5]` (зазор между топом и 5-й позицией). Если зазор < threshold → подключаем cross-encoder.
  3. **Стадия 3 (если включён learned)**: LambdaMART поверх топ-K после стадии 2.
  4. **Стадия 4 (если `personalize=true`)**: профиль-скорринг.
- Экономит 70% rerank-вычислений без потери качества на «уверенных» запросах.
- Включается через `fusion='cascade'` в запросе.
- Confidence-порог не зашит в код, а определяется по историческим nDCG-оценкам — система сама «учится» порогу.

#### 6.2.3. Тенантное tiering

- Не все тенанты одинаково важны. Вводим **tiers**:
  - **Tier A (enterprise)**: dedicated Qdrant shard, SLO p99 = 80 мс, приоритетная очередь индексации, rate limit 500 RPS.
  - **Tier B (standard)**: shared shard, SLO p99 = 150 мс, обычная очередь, rate limit 100 RPS.
  - **Tier C (free)**: shared shard, `write_consistency=1`, SLO p99 = 500 мс (best-effort), rate limit 10 RPS.
- Tier хранится в `tenants` таблице, читается при каждом запросе.

#### 6.2.4. Cost-of-durability trade-off per tier

- `write_consistency = majority` в Qdrant добавляет ~30 мс к каждой записи.
- Для Tier C (free) допустимо `write_consistency = 1` — повышает пропускную способность записи в 3×, ценой возможной потери последней записи при падении одного шарда.
- Осознанный риск, явно зафиксирован в SLA Tier C.

#### 6.2.5. PostgreSQL scaling

- **Read replicas**: 1 primary + 2 read replicas. Чтения — на реплики, запись — на primary.
- **PgBouncer**: transaction-mode, pool = 50 на API-инстанс.
- **Partitioning**: `documents` — hash-партиции по `tenant_id` при > 10M документов.
- **Maintenance**: регулярно `VACUUM ANALYZE`, мониторинг bloat.

#### 6.2.6. Qdrant scaling

- **Sharding**: пересоздать коллекцию с `shard_number = 8` при росте > 10M векторов.
- **Replication**: `replication_factor = 2`, кворум на запись (Tier A/B); `= 1` для Tier C.
- **Quantization**: scalar int8 уже заложен в MVP; при росте — опционально `pq` (product quantization) для дополнительного сжатия.
- **On-disk payload**: payload хранится на диске, payload-индексы — в RAM.

#### 6.2.7. API Layer autoscaling

- Stateless, горизонтально масштабируется по CPU.
- Автоскейлинг по RPS: target = 70% CPU.
- Rate-limiting: 100 RPS на tenant (Tier B), 500 (Tier A), 10 (Tier C) — Redis token bucket.

#### 6.2.8. Embedding Worker autoscaling

- Очередь Redis Streams, consumer groups.
- Параллелизм: 4–8 параллельных воркеров на GPU-ноду, по 32 батча.
- Автоскейлинг по длине очереди: если `XLEN embeddings.queue > 10000` — поднимать ещё поды.

#### 6.2.9. Redis Cluster

- 3 master + 3 replica.
- 16 GB RAM на ноду.
- Eviction: `allkeys-lru`.

#### 6.2.10. Connection-aware routing

- FastAPI-инстанс при старте опрашивает PgBouncer и выбирает наименее загруженную реплику (по `pg_stat_activity` count).
- Не static round-robin, а динамическая балансировка — снижает p99 при неравномерной нагрузке.

#### 6.2.11. CDC migration (Debezium → Kafka → consumer)

- При росте > 100M документов или > 10k RPS записи dual-write становится узким местом.
- Мигрируем на CDC: Debezium читает WAL Postgres → Kafka → consumer пишет в Qdrant.
- **Outbox-структура не меняется** — меняется только потребитель. Это «обратная» подготовка: outbox с первого дня спроектирован так, чтобы стать CDC-ready без schema migration.

#### 6.2.12. Адаптивный `k` в RRF

- Вместо фиксированного `k=60` — `k = 30 + 10 * log(doc_count)` для крупных коллекций.
- Больший `k` сглаживает влияние топовых позиций — полезно при больших выборках кандидатов.

#### 6.2.13. Multi-armed bandit для `α` в Weighted fusion

- Вместо статического `α=0.5` — multi-armed bandit (Thompson sampling) выбирает `α` per-query на основе исторического CTR.
- Сходится к оптимальному `α` под каждый домен без оффлайн-тюнинга.

### 6.3. TRIZ-gates фазы

| Gate | Раздел ARCHITECT.md | Эффект |
|---|---|---|
| Каскадный fusion | §7.5 | Экономит 70% rerank-вычислений |
| Тенантное tiering | §12.6 | Физическое и логическое разделение по tier |
| Connection-aware routing | §12.7 | Динамическая балансировка реплик |
| Cost-of-durability trade-off per tier | §15.6 | `write_consistency` адаптирован под tier |
| CDC readiness (outbox готов к миграции) | §15.5 | Инверсия направления миграции |
| Scalar int8 quantization | §12.2 | 4× сжатие памяти, 2× latency |
| Hash-партиционирование `documents` по `tenant_id` | §12.1 | Локальность данных по тенанту |
| Multi-armed bandit для `α` | §7.2 | Self-tuning без оффлайн-тюнинга |
| Адаптивный `k` в RRF | §7.1 | Подстройка под размер коллекции |

### 6.4. Definition of Done (DoD) — per-компонентно

- **LambdaMART**: обучена на ≥ 4 неделях данных; инференс < 5 мс; nDCG@10 +8–15% над RRF на eval-датасете; A/B подтверждает эффект на реальном трафике.
- **Каскадный fusion**: доля запросов, дошедших до cross-encoder'а, ≤ 30% (vs. 100% при full-rerank); nDCG@10 не ниже full-rerank.
- **Tiering**: Tier A/B/C имеют разные SLO и rate limits, зафиксированные в `tenants` таблице; метрики по tier'ам отдельны в Grafana.
- **PostgreSQL scaling**: PgBouncer держит 500+ RPS/instance; read replicas работают (lag < 5 сек); hash-партиционирование включено при > 10M doc.
- **Qdrant scaling**: shard_number=8 при > 10M векторов; replication_factor=2 (Tier A/B), =1 (Tier C); scalar int8 активен.
- **Redis Cluster**: 6 нод (3 master + 3 replica), HA подтверждена chaos-сценарием «убить master».
- **Connection-aware routing**: p99 ≤ 80 мс для Tier A; распределение нагрузки между репликами равномерное (CV < 0.2).
- **CDC**: trigger (>100M doc или >10k RPS) активирован; outbox-структура не изменилась; lag после миграции ≤ 2 секунд.

### 6.5. Целевые NFR фазы

| Параметр | Целевое значение |
|---|---|
| Latency p99 `/search` (Tier A, без rerank) | ≤ 80 мс |
| Latency p99 `/search` (Tier B, без rerank) | ≤ 150 мс |
| Latency p99 `/search` (Tier C, best-effort) | ≤ 500 мс |
| Throughput индексации | ≥ 5000 doc/min |
| Throughput поиска (Tier A) | ≥ 500 RPS на инстанс |
| Доступность (SLA Tier A) | 99.95% |
| Доступность (SLA Tier B) | 99.9% |
| Доступность (SLA Tier C) | best-effort |
| Lag (после CDC миграции) | ≤ 2 секунды |
| `embedding_cache_hit_rate` | ≥ 70% |

### 6.6. Риски фазы и их mitigations

- **LambdaMART может overfit на исторических данных**: Mitigation — недельный цикл переобучения, holdout-датасет для валидации, A/B перед full rollout.
- **CDC усложняет операционную модель**: Mitigation — миграция только при явном триггере; outbox остаётся safety net параллельно с CDC на переходный период.
- **Tier C `write_consistency=1` теряет данные при падении шарда**: Mitigation — явно зафиксировано в SLA; для критичных free-тенантов — переход на Tier B.
- **Hash-партиционирование `documents` ломает global queries**: Mitigation — глобальные запросы (по всем тенантам) выполняются на primary; tenant-scoped — на репликах с партиционированием.
- **Connection-aware routing может «дрожать» при кратковременных всплесках**: Mitigation — hysteresis (переключение только при стабильном превосходстве альтернативы > 30 секунд).

---

## 7. Сводная матрица фаз × компонентов

| Компонент / подсистема | MVP | Critical | Production-Ready | Improvements |
|---|:---:|:---:|:---:|:---:|
| **PostgreSQL** | | | | |
| `documents` + индексы (tsv, trgm, GIN attrs/tags) | ✅ | — | — | hash-партиции по `tenant_id` |
| `search_outbox` (с `dead`-состоянием) | ✅ | — | digest-воркер hourly | — |
| `embedding_models` (с `is_active`) | ✅ | — | blue-green миграции | — |
| `users` + `profile_vector` | — | — | ✅ | — |
| `search_events` (партиционирование по дням) | структура | — | ✅ + архив в S3 | — |
| `tenant_search_stats` | структура | — | ✅ + adaptive K | — |
| `tenants` с tiers | — | — | — | ✅ |
| Read replicas + PgBouncer | — | — | — | ✅ |
| **Qdrant** | | | | |
| Коллекция `documents` (1024 dim, Cosine) | ✅ | — | — | — |
| Payload-индексы (tenant, lang, tags, attrs, model, time) | ✅ | — | — | — |
| Scalar int8 quantization | ✅ | — | — | — |
| `shard_number=4`, `replication_factor=2` | ✅ | — | — | `shard_number=8`, per-tier consistency |
| Snapshots в S3 (snapshot-on-reindex) | — | — | ✅ | — |
| Blue-green коллекции | — | — | ✅ | — |
| **Embedding Service** | | | | |
| `bge-m3` default | ✅ | — | — | — |
| Content-hash cache + query cache + negative cache | ✅ (первые два) | — | ✅ negative | — |
| Батчинг по 32 | ✅ | — | — | — |
| Асимметричное индексирование (chunking, взвешенное среднее) | — | — | ✅ | — |
| Fast-path по `content_hash` | ✅ | — | — | — |
| **API (FastAPI)** | | | | |
| `POST /search` с RRF | ✅ | — | — | — |
| `POST /index` с dual-write + outbox | ✅ | — | — | — |
| `DELETE /index/{doc_id}` soft-delete | ✅ | — | — | — |
| `GET /index/status/{token}` (read-your-writes) | — | ✅ | — | — |
| `GET /health/live`, `/ready`, `/metrics` | ✅ | — | — | — |
| Weighted fusion с `α` | — | ✅ | — | multi-armed bandit для `α` |
| Cross-encoder rerank | — | ✅ | — | — |
| Speculative rerank | — | ✅ | — | — |
| Каскадный fusion (`fusion='cascade'`) | — | — | — | ✅ |
| `degraded` flag + `partial_result()` | — | ✅ | — | — |
| Adaptive `timeout_ms` | — | ✅ | — | — |
| `SearchContext` (device/session/time) | — | — | ✅ | — |
| `diversify=true` (MMR) | — | — | ✅ | adaptive `λ` |
| `personalize=true` | — | — | ✅ | — |
| **Reconciler / Background Workers** | | | | |
| Basic reconciler (30-секундный интервал, exponential backoff, max 20 attempts → dead) | ✅ | — | — | — |
| Dead-digest-воркер hourly | ✅ | — | — | — |
| Circuit breaker для reranker | — | ✅ | — | — |
| Profile-vector worker (EMA при клике) | — | — | ✅ | adaptive `α` |
| Nightly eval job (Recall@10, nDCG@10) | — | ✅ | — | — |
| Backfill-воркер для миграций | — | — | ✅ | — |
| Канареечный reindex + snapshot-on-reindex | — | — | ✅ | — |
| Shadow traffic | — | — | ✅ | — |
| Chaos engineering weekly | — | — | ✅ | — |
| LambdaMART обучение (weekly) + инференс | — | — | — | ✅ |
| CDC consumer (Debezium → Kafka) | — | — | — | ✅ |
| **Фильтры и фасеты** | | | | |
| Payload-filter в Qdrant (pre-filter) | ✅ | — | — | — |
| Push-down в Postgres (basic, по `reltuples`) | — | ✅ | — | — |
| Push-down с `selectivity_estimate` из `tenant_search_stats` | — | — | ✅ | — |
| Фасеты на top-N в Postgres | — | ✅ | — | — |
| Инкрементальные фасеты (Redis-кэш) | — | — | ✅ | — |
| **Многоязычность** | | | | |
| `langdetect` (basic) | ✅ | — | `fasttext-langid` | — |
| NFC-нормализация + lowercase | ✅ | — | — | — |
| Смешанные запросы (token-level language detection) | — | — | ✅ | — |
| Транслитерация для лексического канала | — | — | ✅ | — |
| Cross-lingual через bge-m3 | ✅ (бесплатно) | — | — | — |
| **Observability** | | | | |
| Prometheus-метрики | ✅ baseline | — | — | — |
| Structured JSON-логи | ✅ | — | — | — |
| Distributed tracing (OTel → Tempo) | — | — | ✅ | — |
| Trace ↔ log correlation (Loki) | — | — | ✅ | — |
| A/B-фреймворк (sticky distribution, sequential testing) | — | — | ✅ | — |
| `dead_letter_count`, `circuit_breaker_open`, `qdrant_pushdown_rate` метрики | ✅ baseline | ✅ | — | — |
| **Scaling / HA** | | | | |
| Single-instance deployment | ✅ | — | — | — |
| Read replicas Postgres | — | — | — | ✅ |
| PgBouncer | — | — | — | ✅ |
| Qdrant cluster sharding | — | — | — | ✅ |
| Redis Cluster (3 master + 3 replica) | — | — | — | ✅ |
| API autoscaling (target 70% CPU) | — | — | — | ✅ |
| Embedding Worker autoscaling (by XLEN) | — | — | — | ✅ |
| Connection-aware routing | — | — | — | ✅ |
| Tenant tiering (A/B/C) | — | — | — | ✅ |
| Cost-of-durability trade-off per tier | — | — | — | ✅ |

**Условные обозначения:** ✅ — компонент внедряется в данной фазе; — — не внедряется (либо уже внедрено ранее, либо не предусмотрено); «структура» — таблица/поле создаются в MVP, наполняются/используются в более поздних фазах.

---

## 8. Карта зависимостей между фазами

Зависимости между компонентами определяют, какие элементы **обязательны** до начала следующих. Например, LambdaMART не может быть обучен без `search_events` (Production-Ready), а CDC-миграция не имеет смысла без outbox-таблицы (MVP).

### 8.1. Жёсткие зависимости (блокирующие)

```
MVP
├─ search_outbox (с dead-состоянием)  ───►  Critical: circuit breaker (нужен lag-метрика)
├─ documents + content_hash           ───►  Critical: fast-path проверки
├─ POST /search с RRF                 ───►  Critical: weighted fusion, cross-encoder rerank
├─ POST /index с dual-write           ───►  Critical: /index/status/{token} (read-your-writes)
├─ embedding_models (is_active)       ───►  Production-Ready: blue-green миграции
├─ POST /search degraded (lex-only)   ───►  Critical: degraded flag в ответе
└─ embedding_cache (Redis)            ───►  Critical: speculative rerank (нужен быстрый query emb)

Critical
├─ Nightly eval (Recall@10, nDCG@10)  ───►  Production-Ready: regression gate для миграций
├─ Cross-encoder на GPU               ───►  Production-Ready: speculative rerank устойчив
├─ Circuit breaker                    ───►  Production-Ready: chaos-сценарий «delay reranker»
├─ Push-down фильтры                  ───►  Production-Ready: selectivity-эвристика из tenant_search_stats
└─ Facets в Postgres                  ───►  Production-Ready: инкрементальные фасеты

Production-Ready
├─ search_events (партиц. по дням)    ───►  Improvements: LambdaMART обучение (нужны кликовые данные)
├─ A/B-фреймворк                       ───►  Improvements: multi-armed bandit для α
├─ tenant_search_stats                 ───►  Improvements: tiering (нужна статистика по тенантам)
├─ Migration framework (blue-green)   ───►  Improvements: CDC readiness (outbox готов к смене потребителя)
└─ Shadow traffic                     ───►  Improvements: канареечный rollout каскадного fusion

Improvements
└─ Все компоненты триггерные — не блокируют дальнейшее развитие
```

### 8.2. Мягкие зависимости (рекомендательные)

Некоторые компоненты можно внедрить раньше или позже без блокировки, но с разным ROI:

- **Adaptive `k` в RRF** (Improvements): можно внедрить в Production-Ready, но нужен `tenant_search_stats` — поэтому мягко зависит от Production-Ready.
- **Speculative rerank** (Critical): можно внедрить в MVP, но без query embedding кэша выигрыш минимален.
- **Chaos engineering weekly** (Production-Ready): можно начать в Critical, но без OTel-инструментации postcondition-чеки будут бедными.
- **Каскадный fusion** (Improvements): можно внедрить в Production-Ready, но без `learned` стадии 3 — каскад вырождается в RRF + опциональный cross-encoder.

### 8.3. Что НЕ зависит от других фаз

Эти компоненты можно внедрять в любой момент, не дожидаясь ничего:

- **Negative cache для embeddings** (Production-Ready, но изолирован) — добавляется в любой момент.
- **MMR diversification** (Production-Ready, но изолирован) — не зависит от персонализации; может работать на pure fusion.
- **Hash-партиционирование `documents`** (Improvements) — можно включить в любой момент при росте данных.
- **Schema drift контракт-тесты** (Production-Ready, но изолирован) — добавляются в CI в любой момент.

---

## 9. Критерии перехода между фазами (exit criteria)

### 9.1. MVP → Critical

Переход разрешён, когда **все** следующие условия выполнены:

- DoD MVP достигнут (см. §3.4).
- NFR MVP достигнуты: latency p99 ≤ 200 мс, throughput ≥ 500 doc/min, lag ≤ 30 сек, SLA 99%.
- Chaos-сценарий «kill Qdrant 60 секунд» проходит: API не возвращает 5xx, переходит в lex-only, восстанавливается после подъёма Qdrant.
- Reconciler разгребает outbox при инъекции 100k pending-записей за < 5 минут.
- Embedding cache hit rate на стационаре ≥ 40%.
- Eval-датасет (≥ 100 размеченных запросов) собран и зафиксирован как baseline.

### 9.2. Critical → Production-Ready

- DoD Critical достигнут (см. §4.4).
- NFR Critical достигнуты: latency p99 ≤ 350 мс с rerank, Recall@10 не падает > 2%, nDCG@10 не падает > 1%, SLA 99.5%.
- Circuit breaker срабатывает автоматически при инъекции искусственной деградации reranker (chaos-сценарий в CI).
- Nightly eval стабильно работает ≥ 2 недели без false-positive алертов.
- `wait_for_index` polling подтверждает read-your-writes в SLO 5 секунд.
- Push-down фильтры демонстрируют 3–5× ускорение на селективных фильтрах.
- Команда подтвердила готовность к более частым релизам (nightly eval как gate, A/B как рутина).

### 9.3. Production-Ready → Improvements

- DoD Production-Ready достигнут (см. §5.4).
- NFR Production-Ready достигнуты: latency p99 ≤ 120 мс (без rerank), SLA 99.9%, lag ≤ 5 сек.
- ≥ 4 недели кликовых данных в `search_events` (для LambdaMART).
- Distributed tracing работает end-to-end: trace из логов Loki открывает trace в Tempo.
- Chaos-сценарии weekly зелёные в CI.
- Минимум одна zero-downtime миграция embedding-модели выполнена успешно (blue-green + канарейка + rollback-тест).
- Триггер для конкретного компонента Improvements активирован (например, `> 1M документов` для PostgreSQL scaling, `> 4 недели данных` для LambdaMART).

### 9.4. Критерии для опциональных компонентов Improvements

| Компонент | Триггер |
|---|---|
| PostgreSQL read replicas + PgBouncer | `> 1M документов` или `> 500 RPS sustained` |
| Qdrant sharding 4 → 8 | `> 10M векторов` в коллекции |
| Redis Cluster | `> 10 GB` кэша или HA requirement |
| Hash-партиционирование `documents` | `> 10M документов` |
| Tenant tiering (A/B/C) | Появление ≥ 1 enterprise-тенанта или ≥ 3 free-тенантов |
| LambdaMART | `≥ 4 недели` кликовых данных в `search_events` |
| Каскадный fusion | Cross-encoder стабилен; метрики по `score[0] - score[5]` собираются ≥ 1 недели |
| CDC (Debezium) | `> 100M документов` или `> 10k RPS записи` |
| Connection-aware routing | p99 на отдельной реплике систематически > 1.5× от средней |
| Multi-armed bandit для `α` | `≥ 1 месяц` A/B-данных по weighted fusion |

---

## 10. TRIZ-gates по фазам

TRIZ-gate — это архитектурное решение, которое **обязательно внедряется вместе с соответствующей фичей**, а не откладывается «на потом». Это предотвращает накопление техдолга: добавить `dead`-состояние в зрелую outbox-таблицу с 1M записей — это миграция, а добавить его на старте — одна колонка в `CREATE TABLE`.

### 10.1. TRIZ-gates MVP

| Принцип ТРИЗ | Gate | Что даёт |
|---|---|---|
| §11 (Заранее подложенная подушка) | `dead`-состояние в `search_outbox` | Безопасный путь отказа для неустранимых ошибок |
| §22 (Обратить вред в пользу) | Dead-digest-агрегатор hourly | Dead-letter → диагностический инструмент |
| §16 (Частичное/избыточное действие) | Fast-path по `content_hash` | Пропуск upsert при совпадении хэша |
| §25 (Самообслуживание) | Reconciler сам находит работу (`SELECT ... WHERE status IN ('pending','failed')`) | Нет нужды в координаторе |
| §19 (Периодическое действие) | Reconciler работает по расписанию (30 сек) | Простой bounded-lag, предсказуемая нагрузка |
| §35 (Изменение параметров) | `content_hash` в схеме `documents` | Снимает необходимость вычислять хэш на лету |

### 10.2. TRIZ-gates Critical

| Принцип ТРИЗ | Gate | Что даёт |
|---|---|---|
| §9 (Предварительное антидействие) | Circuit breaker для reranker | Заранее подложенный механизм отключения |
| §19 (Периодическое действие) | Circuit breaker с окном 60 сек | Периодическое переоткрытие, не permanent fail |
| §21 (Проскочить) | Speculative rerank | Начинаем rerank до завершения fusion |
| §27 (Дешёвая недолговечность) | `wait_for_index_token` | Дешёвый короткоживущий токен для polling |
| §16 (Частичное/избыточное действие) | `partial_result()` при `DeadlineExceeded` | Лучше 5 результатов за 100 мс, чем 0 за 200 мс |
| §9 (Предварительное антидействие) | `degraded` flag + feature-flag `vector_search_enabled` | Заранее заготовлен fallback |
| §23 (Обратная связь) | Nightly eval → regression → блокировка релиза | Замкнутый контур |
| §21 (Проскочить) | 202 Accepted при `pending_count > threshold` | Не блокируем, отдаём 202 |
| §17 (Переход в другое измерение) | Push-down фильтры (Postgres как первичный фильтр) | Третий агент в фильтрации |

### 10.3. TRIZ-gates Production-Ready

| Принцип ТРИЗ | Gate | Что даёт |
|---|---|---|
| §13 (Сделай наоборот) | MMR anti-bubble | «Анти-персонализация» как шаг после персонализации |
| §17 (Переход в другое измерение) | Контекстная персонализация (device/time/session) | Несколько профилей вместо одного |
| §15 (Динамичность) | Adaptive `α` в EMA | `α=0.5` для новых, `α=0.05` для established |
| §22 (Обратить вред в пользу) | Shadow traffic | Двойная нагрузка → безопасная валидация |
| §9 (Предварительное антидействие) | Chaos engineering weekly | Планово ломаем систему заранее |
| §10 (Предварительное действие) | Канареечный reindex (1% перед full) | Предварительная проверка |
| §11 (Заранее подложенная подушка) | Snapshot-on-reindex | Подушка для отката за минуты |
| §26 (Копирование) | Blue-green коллекции | Работаем с копией, оригинал не трогаем |
| §17 (Переход в другое измерение) | Distributed tracing (metrics ↔ logs ↔ traces) | Три измерения observability |
| §23 (Обратная связь) | Adaptive K по `tenant_search_stats` | Слушаем статистику |
| §3 (Местное качество) | Инкрементальные фасеты | Local update вместо global recompute |
| §1 (Сегментация) | Смешанные запросы (token-level) | Каждый сегмент обрабатывается оптимально |
| §3 (Местное качество) | Асимметричное индексирование | Разные стратегии под разный контент |
| §35 (Изменение параметров) | NFC-нормализация | Смена физического представления текста |
| §16 (Частичное/избыточное действие) | Negative cache | Избыточное действие (null) ради частичного эффекта |

### 10.4. TRIZ-gates Improvements

| Принцип ТРИЗ | Gate | Что даёт |
|---|---|---|
| §16 (Частичное/избыточное действие) | Каскадный fusion | Rerank только к неуверенным запросам |
| §13 (Сделай наоборот) | Каскад: стратегия по confidence результата, а не по запросу | Инверсия потока управления |
| §25 (Самообслуживание) | Confidence-порог определяется по историческим nDCG | Система сама учится порогу |
| §1 (Сегментация) | Tenant tiering (A/B/C) | Физическое и логическое разделение |
| §3 (Местное качество) | Per-tier SLO и resources | Не «универсальный» пул, а адаптивный |
| §15 (Динамичность) | Connection-aware routing | Выбор реплики в рантайме |
| §35 (Изменение параметров) | Per-tier `write_consistency` | Один параметр, разные значения |
| §13 (Сделай наоборот) | CDC readiness: outbox готов к смене потребителя | Инверсия направления миграции |

---

## 11. NFR-таргеты по фазам

Сводная таблица целевых NFR на каждой фазе. Достижение NFR текущей фазы — необходимое условие для перехода к следующей.

| Параметр | MVP | Critical | Production-Ready | Improvements (Tier A) |
|---|---|---|---|---|
| Latency p99 `/search` (без rerank) | ≤ 200 мс | ≤ 150 мс | ≤ 120 мс | ≤ 80 мс |
| Latency p99 `/search` (с rerank) | — | ≤ 350 мс | ≤ 350 мс | ≤ 200 мс (Tier A) |
| Throughput индексации | ≥ 500 doc/min | ≥ 1000 doc/min | ≥ 2000 doc/min | ≥ 5000 doc/min |
| Throughput поиска (RPS/instance) | ≥ 100 | ≥ 200 | ≥ 500 | ≥ 500 (Tier A) |
| Доступность (SLA) | 99% | 99.5% | 99.9% | 99.95% (Tier A) |
| Lag Postgres↔Qdrant | ≤ 30 с | ≤ 10 с | ≤ 5 с | ≤ 2 с (после CDC) |
| `embedding_cache_hit_rate` | ≥ 40% | ≥ 50% | ≥ 60% | ≥ 70% |
| Recall@10 (vs baseline) | baseline | не падает > 2% | не падает > 2% | не падает > 2% |
| nDCG@10 (vs baseline) | baseline | не падает > 1% | не падает > 1% | +8–15% (с LambdaMART) |
| Read-your-writes SLO | не гарантируется | 5 сек (polling) | 5 сек | 5 сек |

---

## 12. Риски и mitigations

Сводная карта рисков, актуальных на разных фазах, и их mitigations. Риски, которые уже упомянуты в разделах фаз, здесь собраны в одном месте для удобства аудита.

### 12.1. Архитектурные риски

| Риск | Фаза возникновения | Mitigation | Где реализован |
|---|---|---|---|
| Окно несогласованности dual-write | MVP | Outbox + reconciler + `wait_for_index` polling | §3.2.5, §4.2.7 |
| Schema drift Postgres ↔ Qdrant | MVP | Единый pydantic `QdrantPayload`, контракт-тесты в CI | §5.2.14 |
| Reranker деградирует под нагрузкой | Critical | Circuit breaker + speculative rerank + `timeout_ms` | §4.2.3, §4.2.4 |
| Регрессия качества при смене модели | Critical | Nightly eval + блокировка релиза | §4.2.6 |
| Qdrant недоступен — поиск парализован | Critical | Degraded mode (lex-only) + `degraded` flag | §4.2.8 |
| High-cardinality tenant фильтры медленны в Qdrant | Critical | Push-down в Postgres | §4.2.9 |
| Outbox переполняется при длительном сбое Qdrant | Critical | 202 Accepted throttle + full reindex по watermark | §4.2.10 |
| «Пузырь фильтров» от персонализации | Production-Ready | MMR diversification + adaptive `λ` | §5.2.4 |
| Миграция embedding-модели даёт регрессию | Production-Ready | Канареечный reindex + snapshot-on-reindex + A/B | §5.2.7 |
| GDPR / privacy для `profile_vector` | Production-Ready | Агрегат (не восстанавливает клики), retention 90 дней, GDPR-delete | §5.6 |
| LambdaMART overfit на исторических данных | Improvements | Weekly переобучение, holdout, A/B перед rollout | §6.6 |
| CDC усложняет операционную модель | Improvements | Миграция только по триггеру; outbox остаётся safety net | §6.2.11 |
| Tier C `write_consistency=1` теряет данные | Improvements | Зафиксировано в SLA; переход на Tier B для критичных free-тенантов | §6.6 |

### 12.2. Operational риски

| Риск | Mitigation |
|---|---|
| Потеря задачи в Redis Streams (Redis упал после коммита PG) | Reconciler подбирает по `next_retry_at <= now()` — safety net не зависит от Redis |
| Дублирующая обработка (Reconciler + Worker одновременно) | `FOR UPDATE SKIP LOCKED` + идемпотентный upsert в Qdrant по `doc_id` + версионирование через `content_hash` |
| High `pending_count` в outbox | Adaptive throttle (202) → full reindex по watermark (100k) |
| Qdrant потерян полностью | Full reindex из Postgres; reconciler догоняет рассинхрон после старта reindex |
| Redis cache miss avalanche | Negative cache (60 сек TTL на `null`); staggered TTL |
| Postgres bloat | Регулярный `VACUUM ANALYZE`; мониторинг `pg_stat_user_tables`; при росте — hash-партиционирование |

### 12.3. Quality риски

| Риск | Mitigation |
|---|---|
| Cold-start пользователя — нет персонализации | Pure fusion; history-aware boost как fallback; `α=0.5` для быстрого обучения нового пользователя |
| Cross-lingual поиск ошибается на редких языках | Fallback на лексический канал при `langdetect confidence < 0.7`; LaBSE как альтернативная модель в `embedding_models` |
| Длинные документы теряют детали при одном embedding | Асимметричное индексирование: chunking по 512 токенов с overlap 50 |
| Структурированные документы — неодинаковая ценность секций | Взвешенное среднее: title × 3, abstract × 2, body × 1 |
| Mixed-language запросы («купить iPhone 15 pro в Москве») | Token-level language detection; два параллельных ts-запроса через `tsquery &&` |

---

## 13. Метрики прогресса внедрения

Эти метрики публикуются в Grafana-дашборде «Roadmap Progress» и обновляются при завершении каждого компонента. Они позволяют команде и стейкхолдерам видеть прогресс не в «% готово», а в конкретных эксплуатационных терминах.

### 13.1. Метрики компонентов

| Метрика | Описание | Цель |
|---|---|---|
| `roadmap_components_total{phase}` | Кол-во компонентов в фазе | MVP: 18, Critical: 10, Production-Ready: 15, Improvements: 13 |
| `roadmap_components_done{phase}` | Кол-во завершённых компонентов | Растёт к `total` |
| `roadmap_phase_status` | Статус фазы: `not_started`, `in_progress`, `done` | По одной фазе в `in_progress` |
| `roadmap_triz_gates_done{phase}` | Кол-во внедрённых TRIZ-gates | MVP: 6, Critical: 9, Production-Ready: 15, Improvements: 8 |

### 13.2. Метрики качества (per-фаза)

| Метрика | Когда начинает считаться | Целевой тренд |
|---|---|---|
| `search_latency_p99_ms{phase}` | MVP | Падение от 200 → 80 мс по фазам |
| `index_lag_seconds{phase}` | MVP | Падение от 30 → 2 сек по фазам |
| `sla_percentage{phase}` | Critical | Рост от 99.5% → 99.95% |
| `recall_at_10{phase}` | Critical (nightly eval) | Не падает > 2% относительно baseline |
| `ndcg_at_10{phase}` | Critical (nightly eval) | Не падает > 1%; +8–15% на Improvements |
| `embedding_cache_hit_rate{phase}` | MVP | Рост от 40% → 70% |
| `dead_letter_count` | MVP | 0 в норме; алерт > 0 |
| `circuit_breaker_open` | Critical | 0 в норме; алерт при > 0 > 60 сек |
| `qdrant_pushdown_rate` | Critical | Доля запросов с push-down (мониторинг эвристики) |
| `ab_experiments_active` | Production-Ready | Обычно 1–3 параллельных эксперимента |
| `chaos_scenarios_passed` | Production-Ready | Weekly, 100% зелёных в CI |
| `lambdaMART_inference_ms` | Improvements | < 5 мс p99 |
| `cascade_rerank_skip_rate` | Improvements | 70% запросов не доходят до cross-encoder |

### 13.3. Критерии «фаза завершена» (автоматизированная проверка)

Фаза считается завершённой, когда:

1. Все компоненты из §7 (соответствующая колонка) отмечены `✅`.
2. Все TRIZ-gates из §10 (соответствующий раздел) внедрены.
3. Все NFR из §11 (соответствующая колонка) достигнуты в течение ≥ 2 недель стабильно.
4. Chaos-сценарии для фазы (если есть) — зелёные.
5. DoD фазы (см. §3.4 / §4.4 / §5.4 / §6.4) — выполнен.

Эти критерии проверяются в CI на основе метрик, а не «на глаз». Любой компонент, не достигший целевой метрики, блокирует переход к следующей фазе.

---

## 14. Связь с исходным ARCHITECT.md

Данный roadmap полностью опирается на `ARCHITECT.md` v2.0 (TRIZ-applied). Соответствие разделов:

| Раздел ARCHITECT.md | Соответствие в ROADMAP.md |
|---|---|
| §1. Обзор и цели | §1.1, §1.2 — без изменений |
| §1.3. NFR | §11 (по фазам) |
| §2. Высокоуровневая архитектура | §3.2 (MVP — базовые компоненты) |
| §3. Схема данных | §3.2.1 (PG MVP), §3.2.2 (Qdrant MVP), §5.2.1–5.2.2 (PG Production-Ready) |
| §4. Синхронизация dual-write | §3.2.5 (MVP — basic), §4.2.7 (Critical — `wait_for_index`), §4.2.10 (Critical — throttle) |
| §5. Embedding-пайплайн | §3.2.3 (MVP — basic), §5.2.13 (Production-Ready — асимметричное) |
| §6. Query Pipeline | §3.2.4 (MVP — RRF), §4.2.4 (Critical — speculative), §5.2.10 (Production-Ready — adaptive K) |
| §7. Стратегии Fusion | §3.2.4 (RRF MVP), §4.2.1–4.2.2 (Critical — cross-encoder, weighted), §6.2.2 (Improvements — каскад) |
| §8. Фильтры и фасеты | §4.2.5 (Critical — фасеты), §4.2.9 (Critical — push-down basic), §5.2.11 (Production-Ready — инкрементальные) |
| §9. Многоязычность | §3.2.3 (MVP — bge-m3 мультиязычный), §5.2.12 (Production-Ready — смешанные запросы) |
| §10. Персонализация | §5.2.1–5.2.5 (Production-Ready — все) |
| §11. Observability | §3.2.6 (MVP — baseline), §4.2.6 (Critical — nightly eval), §5.2.6 (Production-Ready — OTel), §5.2.8–5.2.9 (shadow + chaos) |
| §12. Масштабирование | §6.2.5–6.2.9 (Improvements — все) |
| §13. Миграции и Reindex | §5.2.7 (Production-Ready — миграционный фреймворк) |
| §14. API-контракты | §3.2.4 (MVP), §4.2.7–4.2.8 (Critical — `wait_for_index`, degraded), §5.2.5 (Production-Ready — MMR, SearchContext) |
| §15. Риски dual-write | §12 (все риски и mitigations) |
| §16. Roadmap внедрения (исходный) | §1.3 (отличия), §3–§6 (реорганизация по 4 категориям) |
| §17. Приложение: конфиги | Не повторяется; параметры из конфигов отражены в описании компонентов |
| §18. Ссылки | Не повторяется |
| §19. Сводная карта ТРИЗ | §10 (TRIZ-gates по фазам) |

---

*Документ сопровождает `ARCHITECT.md` v2.0. При изменении архитектуры — обновить roadmap синхронно; при изменении NFR — обновить §11.*
