#!/bin/bash

# OriaHS Infrastructure Shutdown Script
#
# Stops the API server and all Docker Compose services.
#
# Usage: ./scripts/stop-infra.sh [options]
# Options:
#   --keep-data     Don't remove Docker volumes (preserve data)
#   --docker-only   Stop only Docker services, leave API server running
#   -v, --verbose   Enable verbose output
#   -h, --help      Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$SCRIPT_DIR/.api-server.pid"

# Defaults
KEEP_DATA=false
DOCKER_ONLY=false
VERBOSE=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    local status="$1"
    local message="$2"
    case "$status" in
        "OK")   echo -e "${GREEN}[OK]${NC}   $message" ;;
        "FAIL") echo -e "${RED}[FAIL]${NC} $message" ;;
        "WARN") echo -e "${YELLOW}[WARN]${NC} $message" ;;
        "INFO") echo -e "${BLUE}[INFO]${NC} $message" ;;
    esac
}

verbose() {
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

show_help() {
    cat << 'EOF'
OriaHS Infrastructure Shutdown Script

Usage: ./scripts/stop-infra.sh [options]

Options:
  --keep-data     Don't remove Docker volumes (preserve data)
  --docker-only   Stop only Docker services, leave API server running
  -v, --verbose   Enable verbose output
  -h, --help      Show this help message
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-data)    KEEP_DATA=true; shift ;;
        --docker-only)  DOCKER_ONLY=true; shift ;;
        -v|--verbose)   VERBOSE=true; shift ;;
        -h|--help)      show_help; exit 0 ;;
        *)              print_status "FAIL" "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Stop API server
stop_app() {
    if [[ "$DOCKER_ONLY" == true ]]; then
        verbose "Skipping API server stop (--docker-only)"
        return 0
    fi

    if [[ ! -f "$PID_FILE" ]]; then
        verbose "No PID file found, API server may not be running"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")

    if kill -0 "$pid" 2>/dev/null; then
        print_status "INFO" "Stopping API server (PID: $pid)..."
        kill "$pid" 2>/dev/null || true

        # Wait up to 5 seconds for graceful shutdown
        local waited=0
        while kill -0 "$pid" 2>/dev/null && [[ $waited -lt 5 ]]; do
            sleep 1
            waited=$((waited + 1))
        done

        # Force kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            print_status "WARN" "Force killing API server..."
            kill -9 "$pid" 2>/dev/null || true
        fi

        print_status "OK" "API server stopped"
    else
        print_status "WARN" "API server (PID: $pid) is not running"
    fi

    rm -f "$PID_FILE"
    rm -f "$SCRIPT_DIR/.api-server.log"
}

# Stop Docker Compose services
stop_docker() {
    print_status "INFO" "Stopping Docker Compose services..."

    cd "$PROJECT_ROOT"

    local down_args=()
    if [[ "$KEEP_DATA" == true ]]; then
        down_args+=("--volumes")
        verbose "Preserving volumes (--keep-data)"
    fi

    if docker compose down "${down_args[@]+"${down_args[@]}"}"; then
        print_status "OK" "Docker Compose services stopped"
    else
        print_status "FAIL" "Failed to stop Docker Compose services"
        exit 1
    fi
}

# Main
main() {
    print_status "INFO" "OriaHS Infrastructure Shutdown"
    echo ""

    stop_app
    stop_docker

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  OriaHS Infrastructure stopped.${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "  Start with: ${YELLOW}./scripts/start-infra.sh${NC}"
    echo ""
}

main "$@"
