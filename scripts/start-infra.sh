#!/bin/bash

# OriaHS Infrastructure Startup Script
#
# Starts all infrastructure services (Docker Compose), runs database migrations,
# and launches the FastAPI application server.
#
# Usage: ./scripts/start-infra.sh [options]
# Options:
#   --no-app        Start only Docker services, skip API server
#   --no-migrate    Skip database migrations
#   --no-sync       Skip uv sync (dependencies install)
#   -v, --verbose   Enable verbose output
#   -h, --help      Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$SCRIPT_DIR/.api-server.pid"
LOG_FILE="$SCRIPT_DIR/.api-server.log"

# Defaults
START_APP=true
RUN_MIGRATE=true
RUN_SYNC=true
VERBOSE=false
HEALTH_TIMEOUT=60
HEALTH_INTERVAL=2
API_PORT="${API_PORT:-8000}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_status() {
    local status="$1"
    local message="$2"
    case "$status" in
        "OK")   echo -e "${GREEN}[OK]${NC}   $message" ;;
        "FAIL") echo -e "${RED}[FAIL]${NC} $message" ;;
        "WARN") echo -e "${YELLOW}[WARN]${NC} $message" ;;
        "INFO") echo -e "${BLUE}[INFO]${NC} $message" ;;
        "WAIT") echo -e "${CYAN}[WAIT]${NC} $message" ;;
    esac
}

verbose() {
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

show_help() {
    cat << 'EOF'
OriaHS Infrastructure Startup Script

Usage: ./scripts/start-infra.sh [options]

Options:
  --no-app        Start only Docker services, skip API server
  --no-migrate    Skip database migrations
  --no-sync       Skip uv sync (dependencies install)
  -v, --verbose   Enable verbose output
  -h, --help      Show this help message
EOF
}

cleanup_on_error() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        print_status "FAIL" "Startup failed (exit code: $exit_code). Check logs above for details."
    fi
}
trap cleanup_on_error EXIT

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-app)      START_APP=false; shift ;;
        --no-migrate)  RUN_MIGRATE=false; shift ;;
        --no-sync)     RUN_SYNC=false; shift ;;
        -v|--verbose)  VERBOSE=true; shift ;;
        -h|--help)     show_help; exit 0 ;;
        *)             print_status "FAIL" "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Pre-flight checks
preflight() {
    verbose "Running pre-flight checks..."

    if [[ ! -f "$PROJECT_ROOT/pyproject.toml" ]]; then
        print_status "FAIL" "Not in project root. pyproject.toml not found."
        exit 1
    fi

    if ! command -v docker &>/dev/null; then
        print_status "FAIL" "Docker is not installed."
        exit 1
    fi

    if ! docker info &>/dev/null; then
        print_status "FAIL" "Docker daemon is not running."
        exit 1
    fi

    if ! command -v uv &>/dev/null; then
        print_status "FAIL" "uv is not installed. Install: pip install uv"
        exit 1
    fi

    verbose "Pre-flight checks passed"
}

# Copy .env if missing
setup_env() {
    if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
        if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
            cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
            print_status "OK" "Created .env from .env.example"
        else
            print_status "WARN" "No .env or .env.example found. Using defaults."
        fi
    else
        verbose ".env already exists"
    fi
}

# Build application image
build_image() {
    print_status "INFO" "Building application image..."

    cd "$PROJECT_ROOT"
    if docker compose build migrate; then
        print_status "OK" "Application image built"
    else
        print_status "FAIL" "Failed to build application image"
        exit 1
    fi
}

# Start Docker Compose services
start_docker() {
    print_status "INFO" "Starting Docker Compose services..."

    cd "$PROJECT_ROOT"
    # Start only infrastructure services (exclude one-shot jobs like migrate/init-qdrant)
    local services=("postgres" "qdrant" "redis" "prometheus" "grafana")
    if docker compose up -d "${services[@]}"; then
        print_status "OK" "Docker Compose services started: ${services[*]}"
    else
        print_status "FAIL" "Failed to start Docker Compose services"
        exit 1
    fi
}

# Create PostgreSQL extensions
create_extensions() {
    print_status "INFO" "Creating PostgreSQL extensions..."
    
    cd "$PROJECT_ROOT"
    # vector extension is not in any migration (001 only creates pg_trgm/pgcrypto)
    if docker compose exec -T postgres psql -U postgres -d orlahs \
        -c "CREATE EXTENSION IF NOT EXISTS vector;" \
        -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
        -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"; then
        print_status "OK" "PostgreSQL extensions created"
    else
        print_status "FAIL" "Failed to create PostgreSQL extensions"
        exit 1
    fi
}

# Wait for all services to become healthy
wait_for_health() {
    print_status "WAIT" "Waiting for services to be healthy (timeout: ${HEALTH_TIMEOUT}s)..."

    local elapsed=0
    local services=("oriahs-postgres" "oriahs-qdrant" "oriahs-redis" "oriahs-prometheus" "oriahs-grafana")

    while [[ $elapsed -lt $HEALTH_TIMEOUT ]]; do
        local all_healthy=true

        for svc in "${services[@]}"; do
            local health
            health=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "missing")
            
            if [[ "$health" != "healthy" && "$health" != "running" ]]; then
                all_healthy=false
                break
            fi
        done

        if [[ "$all_healthy" == true ]]; then
            print_status "OK" "All services are healthy (${elapsed}s)"
            return 0
        fi

        sleep "$HEALTH_INTERVAL"
        elapsed=$((elapsed + HEALTH_INTERVAL))
        printf "\r${CYAN}[WAIT]${NC}   Waiting... %ds / %ds" "$elapsed" "$HEALTH_TIMEOUT"
    done

    echo ""
    print_status "FAIL" "Services did not become healthy within ${HEALTH_TIMEOUT}s"
    docker compose ps
    exit 1
}

# Install Python dependencies
sync_deps() {
    if [[ "$RUN_SYNC" == false ]]; then
        verbose "Skipping uv sync"
        return 0
    fi

    print_status "INFO" "Installing Python dependencies..."
    cd "$PROJECT_ROOT"

    if uv sync; then
        print_status "OK" "Dependencies installed"
    else
        print_status "FAIL" "Failed to install dependencies"
        exit 1
    fi
}

# Run database migrations
run_migrations() {
    if [[ "$RUN_MIGRATE" == false ]]; then
        verbose "Skipping migrations"
        return 0
    fi

    print_status "INFO" "Running database migrations..."
    cd "$PROJECT_ROOT"

    if docker compose run --rm migrate; then
        print_status "OK" "Migrations applied"
    else
        print_status "FAIL" "Migrations failed"
        exit 1
    fi
}

# Initialize Qdrant collection
init_qdrant() {
    print_status "INFO" "Initializing Qdrant collection..."

    cd "$PROJECT_ROOT"
    if docker compose run --rm init-qdrant; then
        print_status "OK" "Qdrant collection created"
    else
        print_status "FAIL" "Qdrant initialization failed"
        exit 1
    fi
}

# Start the FastAPI application server
start_app() {
    if [[ "$START_APP" == false ]]; then
        verbose "Skipping API server start"
        return 0
    fi

    # Stop existing server if running
    if [[ -f "$PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            print_status "WARN" "Stopping existing API server (PID: $old_pid)..."
            kill "$old_pid" 2>/dev/null || true
            sleep 2
        fi
        rm -f "$PID_FILE"
    fi

    print_status "INFO" "Starting API server on port ${API_PORT}..."
    cd "$PROJECT_ROOT"

    nohup uv run uvicorn app.main:app --reload --port "$API_PORT" > "$LOG_FILE" 2>&1 &
    local app_pid=$!
    echo "$app_pid" > "$PID_FILE"

    # Brief check that it started
    sleep 2
    if kill -0 "$app_pid" 2>/dev/null; then
        print_status "OK" "API server started (PID: $app_pid, log: $LOG_FILE)"
    else
        print_status "FAIL" "API server failed to start. Check $LOG_FILE"
        exit 1
    fi
}

# Print summary
print_summary() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  OriaHS Infrastructure is running!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    local pg_port=$(env_value "POSTGRES_PORT" "5433")
    local qdrant_port=$(env_value "QDRANT_PORT" "6333")
    local redis_port=$(env_value "REDIS_PORT" "6379")
    local grafana_port=$(env_value "GRAFANA_PORT" "3000")
    
    echo -e "  PostgreSQL : ${CYAN}localhost:${pg_port}${NC}"
    echo -e "  Qdrant     : ${CYAN}localhost:${qdrant_port}${NC}"
    echo -e "  Redis      : ${CYAN}localhost:${redis_port}${NC}"
    echo -e "  Grafana    : ${CYAN}localhost:${grafana_port}${NC}"
    if [[ "$START_APP" == true ]]; then
        echo -e "  API Server : ${CYAN}localhost:${API_PORT}${NC}"
    fi
    echo ""
    echo -e "  Stop with: ${YELLOW}./scripts/stop-infra.sh${NC}"
    echo ""
}

# Read value from .env file
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

# Main
main() {
    print_status "INFO" "OriaHS Infrastructure Startup"
    print_status "INFO" "Project root: $PROJECT_ROOT"
    echo ""

    preflight
    setup_env
    build_image
    start_docker
    wait_for_health
    create_extensions
    run_migrations
    init_qdrant
    sync_deps
    start_app
    print_summary
}

main "$@"
