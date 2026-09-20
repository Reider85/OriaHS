# OriaHS — MVP гибридного поиска (PostgreSQL + Qdrant)

Бэкенд гибридного поиска: лексический канал (PostgreSQL 16 + pg_trgm + tsvector) и
векторный канал (Qdrant 1.x + bge-m3), синхронизированные через dual-write c
outbox-восстановлением.

> Стек: Python 3.11+ (FastAPI, SQLAlchemy 2.x async), PostgreSQL 16, Qdrant 1.x, Redis 7.
> Пакетный менеджер: **uv**.

## Быстрый старт

### 1. Поднять инфраструктуру

```bash
cp .env.example .env
docker compose up -d
```

Проверить, что PostgreSQL расширения доступны:

```bash
docker compose exec postgres psql -U postgres -d orlahs -c \
  "CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
```

> Примечание: расширение `pgvector` подключается как SQL-расширение `vector`
> (это официальное имя в PostgreSQL; `CREATE EXTENSION pgvector` не существует).
> Образ `pgvector/pgvector:pg16` содержит `vector` из коробки.

### 2. Установить зависимости и поднять API

```bash
uv sync                 # создаёт .venv и ставит зависимости
uv run uvicorn app.main:app --reload --port 8000
```

Проверить: `curl http://localhost:8000/` → `{"status":"ok"}`.

### 3. Миграции

```bash
uv run alembic upgrade head
```

### 4. Тесты и качество

```bash
uv run ruff check app/ tests/
uv run mypy app/
uv run pytest
```

## Структура

```
docker-compose.yml      # postgres:16 (pgvector) + qdrant + redis:7
pyproject.toml          # зависимости (uv)
.env.example            # все env-переменные с примерами
alembic/                # SQL-миграции (async engine)
app/
  main.py               # FastAPI app factory create_app()
  config.py             # pydantic-settings (env > .env)
  db/session.py         # async engine + session
  api/routes/health.py  # health stubs (наполняются в P-12)
tests/
```

## Соглашения

- Только async SQLAlchemy (asyncpg), никакого sync-движка.
- Все порты/хосты — через pydantic-settings, без хардкодов.
- Миграции — только через Alembic (идемпотентные, с downgrade).

Подробности: `analitics/ARCHITECT.md`, `analitics/ROADMAP.md`, `analitics/MVP-PROMPTS.md`.