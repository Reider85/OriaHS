# syntax=docker/dockerfile:1
#
# Образ приложения OriaHS — общий для всех процессов стека:
#   api, reconciler, digest (различаются только `command` в docker-compose.yml)
#   migrate, init-qdrant, seed-eval, nightly-eval (one-shot jobs)
#
# Путь /app в builder и runtime совпадает намеренно: `uv sync` ставит проект
# в editable-режиме, и .pth-ссылка должна указывать на реальный каталог.

ARG PYTHON_VERSION=3.12
# Версия uv обязана совпадать с хостовой: uv.lock помечен `revision = 3`,
# старые uv его не читают и падают на --frozen.
ARG UV_VERSION=0.12.19

# ---------- builder: зависимости ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

# Сначала только манифесты — слой с зависимостями переиспользуется, пока
# не меняются pyproject.toml / uv.lock.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY eval ./eval
COPY qdrant_collections.yaml ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- runtime: только venv + исходники ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

# libgomp1 — runtime-зависимость torch (BLAS/OpenMP), без неё падает импорт.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/cache/huggingface \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

# HF_HOME вне /app: кэш моделей (BAAI/bge-m3, bge-reranker-v2-m3) монтируется
# именованным томом и переживает пересборку образа.
RUN mkdir -p /cache/huggingface && chown -R app:app /cache

USER app

EXPOSE 8000

# HEALTHCHECK намеренно не задан в образе: он же используется reconciler,
# digest и one-shot jobs, где HTTP-сервера нет. Проба живёт в
# docker-compose.yml (сервис `api`, /health/live).
CMD ["uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8000"]
