#!/usr/bin/env bash
#
# Bring up the OriaHS stack (MVP infra + CRITICAL app services).
#
# Идемпотентен: безопасно запускать повторно. Каждый шаг можно отключить
# флагом — скрипт не «лечит» состояние молча, а печатает, что именно сделал.
#
# Что делает:
#   1. .env из .env.example (если отсутствует)
#   2. docker compose build + up -d (инфра + профиль app)
#   3. ожидание healthcheck'ов
#   4. CREATE EXTENSION IF NOT EXISTS vector   (её нет ни в одной миграции)
#   5. alembic upgrade head                    (включая 005_eval_datasets, C-07)
#   6. scripts/init_qdrant.py                  (коллекция documents, P-04)
#   7. опционально scripts/seed_eval_corpus.py (C-07: документы под eval)
#   8. GET /health/ready с ретраями + сводка URL
#
# Usage: ./scripts/infra_up.sh [options]
# Options:
#   --no-app              только инфраструктура (postgres, qdrant, redis,
#                         prometheus, grafana) — без api/reconciler/digest
#   --no-build            не пересобирать образ
#   --rebuild             принудительная пересборка образа (без кэша)
#   --skip-migrations     не запускать alembic upgrade head
#   --skip-init-qdrant    не создавать коллекцию Qdrant
#   --skip-extensions     не создавать PG-расширения
#   --seed-eval           заполнить БД eval-корпусом (C-07) и дождаться
#                         опустошения outbox
#   --nightly             сразу прогнать ночной оффлайн-eval (C-07)
#   --timeout N           таймаут ожидания healthcheck'ов, секунд (default: 240)
#   -v, --verbose         подробный вывод команд
#   -h, --help            справка
#
# Exit codes:
#   0 — стек поднят
#   1 — шаг завершился ошибкой
#   2 — окружение не готово (нет docker / нет .env и нечего создать)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../" && pwd)"
cd "$PROJECT_ROOT"

WITH_APP=true
DO_BUILD=true
REBUILD=false
SKIP_MIGRATIONS=false
SKIP_INIT_QDRANT=false
SKIP_EXTENSIONS=false
SEED_EVAL=false
RUN_NIGHTLY=false
TIMEOUT_SECONDS=240
VERBOSE=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    case "$1" in
        PASS) echo -e "${GREEN}[PASS]${NC} $2" ;;
        FAIL) echo -e "${RED}[FAIL]${NC} $2" ;;
        WARN) echo -e "${YELLOW}[WARN]${NC} $2" ;;
        INFO) echo -e "${BLUE}[INFO]${NC} $2" ;;
        STEP) echo -e "${BLUE}==>${NC} ${2}" ;;
    esac
}

verbose() { [[ "$VERBOSE" == true ]] && echo -e "${BLUE}[VERBOSE]${NC} $1"; return 0; }

show_help() {
    cat << 'EOF'
Bring up the OriaHS stack (MVP infra + CRITICAL app services).

Idempotent: safe to re-run. Steps:
  1. .env из .env.example (если отсутствует)
  2. docker compose build + up -d (инфраструктура + профиль app)
  3. ожидание healthcheck'ов
  4. CREATE EXTENSION IF NOT EXISTS vector (её нет ни в одной миграции)
  5. alembic upgrade head (включая 005_eval_datasets, C-07)
  6. scripts/init_qdrant.py (коллекция documents, P-04)
  7. --seed-eval: scripts/seed_eval_corpus.py (C-07) + ожидание outbox
  8. GET /health/ready с ретраями + сводка URL

Usage: ./scripts/infra_up.sh [options]
Options:
  --no-app              только инфраструктура (postgres, qdrant, redis,
                        prometheus, grafana) — без api/reconciler/digest
  --no-build            не пересобирать образ
  --rebuild             принудительная пересборка образа (без кэша)
  --skip-migrations     не запускать alembic upgrade head
  --skip-init-qdrant    не создавать коллекцию Qdrant
  --skip-extensions     не создавать PG-расширения
  --seed-eval           заполнить БД eval-корпусом (C-07) и дождаться
                        опустошения outbox
  --nightly             сразу прогнать ночной оффлайн-eval (C-07)
  --timeout N           таймаут ожидания healthcheck'ов, секунд (default: 240)
  -v, --verbose         подробный вывод команд
  -h, --help            справка

Exit codes:
  0 — стек поднят
  1 — шаг завершился ошибкой
  2 — окружение не готово (нет docker / нет .env и нечего создать)
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-app)           WITH_APP=false; shift ;;
        --no-build)         DO_BUILD=false; shift ;;
        --rebuild)          REBUILD=true; DO_BUILD=true; shift ;;
        --skip-migrations)  SKIP_MIGRATIONS=true; shift ;;
        --skip-init-qdrant) SKIP_INIT_QDRANT=true; shift ;;
        --skip-extensions)  SKIP_EXTENSIONS=true; shift ;;
        --seed-eval)        SEED_EVAL=true; shift ;;
        --nightly)          RUN_NIGHTLY=true; shift ;;
        --timeout)          TIMEOUT_SECONDS="$2"; shift 2 ;;
        -v|--verbose)       VERBOSE=true; shift ;;
        -h|--help)          show_help; exit 0 ;;
        *) echo "Unknown option: $1"; show_help; exit 2 ;;
    esac
done

DC=(docker compose --project-directory "$PROJECT_ROOT" -f "$PROJECT_ROOT/docker-compose.yml")
if [[ "$WITH_APP" == true ]]; then
    DC+=(--profile app)
fi

run() {
    verbose "RUN: $*"
    "$@"
}

# Читает значение переменной из .env (без «питоновских» хрупких конструкций).
env_value() {
    local key="$1" fallback="${2:-}"
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
        local raw
        raw="$(grep -E "^[[:space:]]*${key}=" "$PROJECT_ROOT/.env" | tail -n 1 | cut -d= -f2- || true)"
        raw="${raw%%$'\r'}"
        if [[ -n "$raw" ]]; then
            echo "$raw"
            return 0
        fi
    fi
    echo "$fallback"
}

# --- 1. preflight ------------------------------------------------------------
preflight() {
    print_status "STEP" "Preflight"

    if ! command -v docker >/dev/null 2>&1; then
        print_status "FAIL" "docker is not installed (https://docs.docker.com/get-docker/)"
        exit 2
    fi
    if ! docker compose version >/dev/null 2>&1; then
        print_status "FAIL" "docker compose v2 is not available (the v1 'docker-compose' binary is not supported)"
        exit 2
    fi
    if ! docker info >/dev/null 2>&1; then
        print_status "FAIL" "docker daemon is not running"
        exit 2
    fi
    print_status "PASS" "docker: $(docker --version), $(docker compose version --short 2>/dev/null || echo compose v2)"

    if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
        if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
            cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
            print_status "PASS" "created .env from .env.example"
        else
            print_status "FAIL" "neither .env nor .env.example found"
            exit 2
        fi
    else
        print_status "PASS" ".env present"
    fi

    # compose 2.24+ — иначе не понимает `env_file: [{path: .env, required: false}]`
    local compose_minor
    compose_minor="$(docker compose version --short 2>/dev/null | sed 's/^v\([0-9]*\)\.\([0-9]*\).*/\1 \2/' | awk '{print $1 * 100 + $2}')"
    if [[ -n "$compose_minor" ]] && [[ "$compose_minor" -lt 224 ]]; then
        print_status "WARN" "docker compose < 2.24: long-form env_file may be rejected; upgrade if 'config' fails"
    fi
}

# --- 2. build + up -----------------------------------------------------------
start_containers() {
    print_status "STEP" "Starting containers"

    if [[ "$DO_BUILD" == true && "$WITH_APP" == true ]]; then
        if [[ "$REBUILD" == true ]]; then
            print_status "INFO" "Rebuilding application image (--rebuild)..."
            run "${DC[@]}" build --pull --no-cache migrate || exit 1
        else
            print_status "INFO" "Building application image..."
            run "${DC[@]}" build migrate || exit 1
        fi
    fi

    local services=(postgres qdrant redis prometheus grafana)
    if [[ "$WITH_APP" == true ]]; then
        services+=(api reconciler digest)
    else
        print_status "INFO" "--no-app: skipping api, reconciler, digest"
    fi

    # one-shot jobs (migrate / init-qdrant) исключены намеренно: они
    # запускаются ниже отдельными шагами, чтобы их вывод был виден.
    run "${DC[@]}" up -d --remove-orphans "${services[@]}" || exit 1
    print_status "PASS" "containers started: ${services[*]}"
}

# --- 3. healthcheck wait -----------------------------------------------------
wait_for_services() {
    print_status "STEP" "Waiting for healthchecks (timeout ${TIMEOUT_SECONDS}s)"

    local services=(postgres qdrant redis prometheus grafana)
    [[ "$WITH_APP" == true ]] && services+=(api)

    local deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
    local pending=("${services[@]}")
    while [[ $(date +%s) -lt $deadline ]]; do
        pending=()
        for service in "${services[@]}"; do
            # пустое состояние = ещё не создан; ищем контейнер по имени
            local cid
            cid="$("${DC[@]}" ps -q "$service" 2>/dev/null || true)"
            if [[ -z "$cid" ]]; then
                pending+=("$service"); continue
            fi
            local state
            state="$(docker inspect -f '{{.State.Status}}{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || echo missing)"
            if [[ "$state" != "runninghealthy" && "$state" != "running" ]]; then
                pending+=("$service")
            fi
        done

        if [[ ${#pending[@]} -eq 0 ]]; then
            print_status "PASS" "all services healthy: ${services[*]}"
            return 0
        fi
        verbose "not ready yet: ${pending[*]}"
        sleep 3
    done

    print_status "FAIL" "timeout ${TIMEOUT_SECONDS}s waiting for: ${pending[*]}"
    print_status "INFO" "logs: ${DC[*]} logs --tail=50 ${pending[*]}"
    return 1
}

# --- 4. PG extensions --------------------------------------------------------
create_extensions() {
    print_status "STEP" "PostgreSQL extensions"
    if [[ "$SKIP_EXTENSIONS" == true ]]; then
        print_status "INFO" "--skip-extensions"
        return 0
    fi

    local pg_user pg_db
    pg_user="$(env_value POSTGRES_USER postgres)"
    pg_db="$(env_value POSTGRES_DB orlahs)"

    # vector нет ни в одной миграции (001 создаёт только pg_trgm/pgcrypto),
    # а эмбеддинги в documents.qdrant_payload без него не появятся.
    if run "${DC[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U "$pg_user" -d "$pg_db" \
        -c "CREATE EXTENSION IF NOT EXISTS vector;" \
        -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
        -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" >/dev/null; then
        print_status "PASS" "vector, pg_trgm, pgcrypto available"
    else
        print_status "FAIL" "could not create PostgreSQL extensions"
        return 1
    fi
}

# --- 5. миграции -------------------------------------------------------------
run_migrations() {
    print_status "STEP" "Database migrations (alembic upgrade head)"
    if [[ "$SKIP_MIGRATIONS" == true ]]; then
        print_status "INFO" "--skip-migrations"
        return 0
    fi
    if run "${DC[@]}" run --rm migrate; then
        print_status "PASS" "schema is at head"
    else
        print_status "FAIL" "alembic upgrade head failed"
        return 1
    fi
}

# --- 6. коллекция Qdrant -----------------------------------------------------
init_qdrant() {
    print_status "STEP" "Qdrant collection (scripts/init_qdrant.py)"
    if [[ "$SKIP_INIT_QDRANT" == true ]]; then
        print_status "INFO" "--skip-init-qdrant"
        return 0
    fi
    if run "${DC[@]}" run --rm init-qdrant; then
        print_status "PASS" "collection matches qdrant_collections.yaml"
    else
        print_status "FAIL" "init_qdrant.py failed — vector search will not work"
        return 1
    fi
}

# --- 7. eval-корпус ----------------------------------------------------------
seed_eval() {
    print_status "STEP" "Seeding eval corpus (C-07)"
    if [[ "$SEED_EVAL" != true ]]; then
        return 0
    fi
    if run "${DC[@]}" run --rm seed-eval; then
        print_status "PASS" "eval documents inserted"
    else
        print_status "FAIL" "seed_eval_corpus.py failed"
        return 1
    fi

    if [[ "$WITH_APP" != true ]]; then
        print_status "WARN" "reconciler is not running (--no-app): Qdrant will stay empty until 'infra_up.sh' without --no-app"
        return 0
    fi

    print_status "INFO" "waiting for the reconciler to drain the outbox (CPU embedding is slow)..."
    local pg_user pg_db deadline pending
    pg_user="$(env_value POSTGRES_USER postgres)"
    pg_db="$(env_value POSTGRES_DB orlahs)"
    deadline=$(( $(date +%s) + 600 ))
    pending="?"
    while [[ $(date +%s) -lt $deadline ]]; do
        # без run(): verbose-строка не должна попасть в значение
        pending="$("${DC[@]}" exec -T postgres psql -U "$pg_user" -d "$pg_db" -tAc \
            "SELECT count(*) FROM search_outbox WHERE status IN ('pending','failed','in_progress');" \
            2>/dev/null | tr -d '[:space:]' || true)"
        if [[ "$pending" == "0" ]]; then
            print_status "PASS" "outbox drained — Qdrant is in sync"
            return 0
        fi
        verbose "outbox pending: ${pending:-?}"
        sleep 5
    done
    print_status "WARN" "outbox not drained in 600s (pending rows: ${pending:-?})"
    return 0
}

# --- 8. ночной eval ----------------------------------------------------------
run_nightly() {
    [[ "$RUN_NIGHTLY" == true ]] || return 0
    print_status "STEP" "Nightly offline eval (C-07)"
    if run "${DC[@]}" --profile nightly run --rm nightly-eval; then
        print_status "PASS" "nightly eval passed, no regression"
    else
        print_status "FAIL" "nightly eval reported a regression (or failed)"
        return 1
    fi
}

# --- 9. readiness + сводка ---------------------------------------------------
# curl есть не везде (минимальные образы, Windows без Git Bash) — падаем на
# python из venv хоста, иначе на голый TCP-проб порта.
http_get() {
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 5 "$url" 2>/dev/null || true
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$url" <<'PY' 2>/dev/null || true
import sys, urllib.request
print(urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())
PY
    elif command -v python >/dev/null 2>&1; then
        python -c "import sys,urllib.request;print(urllib.request.urlopen(sys.argv[1],timeout=5).read().decode())" "$url" 2>/dev/null || true
    fi
}

check_readiness() {
    print_status "STEP" "Readiness"
    if [[ "$WITH_APP" != true ]]; then
        print_status "INFO" "api not started (--no-app), skipping /health/ready"
        return 0
    fi

    local api_port deadline body
    api_port="$(env_value API_PORT 8000)"
    deadline=$(( $(date +%s) + 120 ))

    while [[ $(date +%s) -lt $deadline ]]; do
        body="$(http_get "http://localhost:${api_port}/health/ready")"
        if [[ -n "$body" ]]; then
            print_status "PASS" "/health/ready: ${body}"
            return 0
        fi
        sleep 3
    done
    print_status "FAIL" "/health/ready did not answer on port ${api_port}"
    print_status "INFO" "logs: ${DC[*]} logs --tail=50 api"
    return 1
}

warn_about_reranker() {
    local mock warmup
    mock="$(env_value RERANKER_MOCK_MODE false)"
    warmup="$(env_value RERANKER_WARMUP false)"
    if [[ "$mock" == "true" ]]; then
        print_status "WARN" "RERANKER_MOCK_MODE=true: rerank returns RRF scores, not cross-encoder scores"
    elif [[ "$warmup" != "true" ]]; then
        print_status "WARN" "RERANKER_WARMUP=false: the first reranked search downloads BAAI/bge-reranker-v2-m3 (~2.3 GB) on the API container"
    fi
}

summary() {
    local api_port pg_port qdrant_port prom_port grafana_port
    api_port="$(env_value API_PORT 8000)"
    pg_port="$(env_value POSTGRES_PORT 5432)"
    qdrant_port="$(env_value QDRANT_PORT 6333)"
    prom_port="$(env_value PROMETHEUS_PORT 9090)"
    grafana_port="$(env_value GRAFANA_PORT 3000)"

    echo
    print_status "PASS" "stack is up"
    echo "  API           http://localhost:${api_port}/docs        (/metrics, /health/live, /health/ready)"
    echo "  Qdrant        http://localhost:${qdrant_port}/dashboard"
    echo "  Prometheus    http://localhost:${prom_port}            (targets: /api/v1/targets)"
    echo "  Grafana       http://localhost:${grafana_port}          (user: ${GRAFANA_USER:-admin}, password: \$GRAFANA_PASSWORD)"
    echo "  PostgreSQL    localhost:${pg_port}"
    echo
    echo "  Dashboards: 4 MVP + 4 CRITICAL (reranker, eval-regression, pushdown, circuit-breaker)"
    echo "  Stop:       ./scripts/infra_down.sh   (add -v to drop volumes)"
}

main() {
    print_status "INFO" "OriaHS infra up — project root: $PROJECT_ROOT"

    preflight
    start_containers
    wait_for_services || exit 1
    create_extensions || exit 1
    run_migrations || exit 1
    init_qdrant || exit 1
    seed_eval || exit 1
    run_nightly || exit 1
    check_readiness || exit 1
    warn_about_reranker
    summary
}

main "$@"
