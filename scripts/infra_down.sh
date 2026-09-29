#!/usr/bin/env bash
#
# Stop the OriaHS stack, rolling the database schema back to base.
#
# Порядок важен: `alembic downgrade base` выполняется ДО остановки контейнеров,
# иначе БД недоступна и откат невозможен. Данные при этом НЕ удаляются —
# том pgdata остаётся, чтобы `infra_up.sh` поднял стек на прежней схеме.
#
# Что делает:
#   1. alembic downgrade base  (001..005 откатываются, расширения остаются —
#                              так и задумано в 001_documents.downgrade())
#   2. docker compose down --remove-orphans (все профили)
#   3. -v/--volumes: снос pgdata, qdrant_data, redis_data, prometheus_data,
#      grafana_data, hf_cache
#   4. --purge: дополнительно удаляет локальный образ приложения и build-кэш
#
# Usage: ./scripts/infra_down.sh [options]
# Options:
#   --skip-migrations   не откатывать миграции (БД уже недоступна)
#   --keep-volumes      явно оставить тома (поведение по умолчанию)
#   -v, --volumes       удалить тома (необратимо)
#   --purge             удалить образ oriahs:* и build-кэш
#   --no-remove-orphans не трогать контейнеры вне текущего compose-файла
#   --timeout N         таймаут остановки контейнеров, секунд (default: 30)
#   -h, --help          справка
#
# Exit codes:
#   0 — стек остановлен
#   1 — ошибка остановки
#   2 — docker недоступен

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../" && pwd)"
cd "$PROJECT_ROOT"

SKIP_MIGRATIONS=false
DROP_VOLUMES=false
PURGE=false
REMOVE_ORPHANS=true
TIMEOUT_SECONDS=30

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

show_help() {
    cat << 'EOF'
Stop the OriaHS stack, rolling the database schema back to base.

alembic downgrade base runs BEFORE the containers are stopped (otherwise the
database is already gone). Data is preserved: the pgdata volume survives, so
`infra_up.sh` brings the stack back on the previous schema.

Steps:
  1. alembic downgrade base   (001..005 down; PG extensions stay by design)
  2. docker compose down --remove-orphans (all profiles)
  3. -v/--volumes: drop pgdata, qdrant_data, redis_data, prometheus_data,
     grafana_data, hf_cache
  4. --purge: also remove the local application image and build cache

Usage: ./scripts/infra_down.sh [options]
Options:
  --skip-migrations   не откатывать миграции (БД уже недоступна)
  --keep-volumes      явно оставить тома (поведение по умолчанию)
  -v, --volumes       удалить тома (необратимо)
  --purge             удалить образ oriahs:* и build-кэш
  --no-remove-orphans не трогать контейнеры вне текущего compose-файла
  --timeout N         таймаут остановки контейнеров, секунд (default: 30)
  -h, --help          справка

Exit codes:
  0 — стек остановлен
  1 — ошибка остановки
  2 — docker недоступен
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-migrations)   SKIP_MIGRATIONS=true; shift ;;
        --keep-volumes)      DROP_VOLUMES=false; shift ;;
        -v|--volumes)        DROP_VOLUMES=true; shift ;;
        --purge)             PURGE=true; shift ;;
        --no-remove-orphans) REMOVE_ORPHANS=false; shift ;;
        --timeout)           TIMEOUT_SECONDS="$2"; shift 2 ;;
        -h|--help)           show_help; exit 0 ;;
        *) echo "Unknown option: $1"; show_help; exit 2 ;;
    esac
done

DC=(docker compose --project-directory "$PROJECT_ROOT" -f "$PROJECT_ROOT/docker-compose.yml")
DOWN_ARGS=(down --timeout "$TIMEOUT_SECONDS")
[[ "$REMOVE_ORPHANS" == true ]] && DOWN_ARGS+=(--remove-orphans)

# Все профили: app-сервисы и one-shot jobs (nightly/tools) не должны остаться
# висеть после остановки.
DC_ALL=("${DC[@]}" --profile app --profile tools --profile nightly)

# Откат схемы. Предупреждение, а не ошибка: если БД уже остановлена, откат
# невозможен, но и падать из-за этого нельзя — пользователь всё равно хочет
# погасить стек.
downgrade_schema() {
    print_status "STEP" "Rolling the schema back (alembic downgrade base)"
    if [[ "$SKIP_MIGRATIONS" == true ]]; then
        print_status "INFO" "--skip-migrations"
        return 0
    fi

    local pg_running=false
    if [[ -n "$("${DC[@]}" ps -q postgres 2>/dev/null || true)" ]]; then
        pg_running=true
    fi
    if [[ "$pg_running" != true ]]; then
        print_status "WARN" "postgres container is not running — cannot downgrade; use 'alembic downgrade base' manually if the schema matters"
        return 0
    fi

    if "${DC[@]}" run --rm migrate alembic downgrade base; then
        print_status "PASS" "schema is at base (001..005 rolled back; PG extensions kept by design)"
    else
        print_status "WARN" "alembic downgrade base failed — continuing with shutdown"
        print_status "INFO" "logs: ${DC[*]} logs --tail=50 migrate"
    fi
}

stop_stack() {
    print_status "STEP" "Stopping containers"
    if "${DC_ALL[@]}" "${DOWN_ARGS[@]}"; then
        print_status "PASS" "containers stopped"
    else
        print_status "FAIL" "docker compose down failed"
        return 1
    fi
}

drop_volumes() {
    if [[ "$DROP_VOLUMES" != true ]]; then
        print_status "INFO" "volumes kept (pgdata, qdrant_data, redis_data, prometheus_data, grafana_data, hf_cache)"
        return 0
    fi
    print_status "STEP" "Removing volumes"
    local down_volumes=("${DC_ALL[@]}" down --volumes --timeout "$TIMEOUT_SECONDS")
    [[ "$REMOVE_ORPHANS" == true ]] && down_volumes+=(--remove-orphans)
    if "${down_volumes[@]}"; then
        print_status "PASS" "volumes removed — data is gone"
    else
        print_status "FAIL" "could not remove volumes"
        return 1
    fi
}

purge_images() {
    if [[ "$PURGE" != true ]]; then
        return 0
    fi
    print_status "STEP" "Removing images and build cache"
    local image="oriahs:dev"
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
        image="$(grep -E '^[[:space:]]*APP_IMAGE=' "$PROJECT_ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d '[:space:]' || true)"
        image="${image:-oriahs:dev}"
    fi
    if docker image inspect "$image" >/dev/null 2>&1; then
        if docker image rm -f "$image" >/dev/null 2>&1; then
            print_status "PASS" "image ${image} removed"
        else
            print_status "WARN" "could not remove image ${image}"
        fi
    else
        print_status "INFO" "image ${image} not present locally"
    fi
    if docker builder prune -f >/dev/null 2>&1; then
        print_status "PASS" "build cache pruned"
    else
        print_status "WARN" "could not prune build cache"
    fi
}

preflight() {
    if ! command -v docker >/dev/null 2>&1; then
        print_status "FAIL" "docker is not installed"
        exit 2
    fi
    if ! docker compose version >/dev/null 2>&1; then
        print_status "FAIL" "docker compose v2 is not available"
        exit 2
    fi
    if ! docker info >/dev/null 2>&1; then
        print_status "FAIL" "docker daemon is not running"
        exit 2
    fi
}

report() {
    echo
    print_status "PASS" "stack is down"
    echo "  up again:  ./scripts/infra_up.sh"
    echo "  from zero: ./scripts/infra_up.sh --rebuild  (после down -v)"
}

main() {
    print_status "INFO" "OriaHS infra down — project root: $PROJECT_ROOT"
    preflight
    downgrade_schema
    stop_stack
    drop_volumes
    purge_images
    report
}

main
