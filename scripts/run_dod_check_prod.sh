#!/bin/bash

# Daily Operations of Done (DOD) check script for production
# Runs critical tests to ensure system health before deployment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/dod_check_$(date +%Y%m%d_%H%M%S).log"
TIMESTAMP_FILE="$LOG_DIR/last_dod_check.timestamp"

# Create log directory
mkdir -p "$LOG_DIR"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to cleanup background processes
cleanup() {
    log "Cleaning up background processes..."
    jobs -p | xargs -r kill || true
    log "Cleanup completed"
}

# Set up trap for cleanup
trap cleanup EXIT

# Function to check if tests should run
should_run_tests() {
    if [ ! -f "$TIMESTAMP_FILE" ]; then
        return 0
    fi
    
    last_run=$(cat "$TIMESTAMP_FILE")
    current_time=$(date +%s)
    time_diff=$((current_time - last_run))
    
    # Run tests if last run was more than 24 hours ago
    if [ $time_diff -gt 86400 ]; then
        return 0
    fi
    
    return 1
}

# Function to update timestamp
update_timestamp() {
    date +%s > "$TIMESTAMP_FILE"
}

# Main execution
log "Starting Daily Operations of Done (DOD) check"

# Check if tests should run
if ! should_run_tests; then
    log "DOD check already run in the last 24 hours, skipping"
    exit 0
fi

# Activate virtual environment if it exists
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    log "Activating virtual environment"
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# Change to project directory
cd "$PROJECT_ROOT"

# Run linting
log "Running linting with ruff..."
if ruff check app/ tests/ scripts/; then
    log "✓ Linting passed"
else
    log "✗ Linting failed"
    exit 1
fi

# Run formatting check
log "Running code formatting check with ruff..."
if ruff format --check app/ tests/ scripts/; then
    log "✓ Code formatting passed"
else
    log "✗ Code formatting failed"
    exit 1
fi

# Run type checking
log "Running type checking with mypy..."
if mypy app/; then
    log "✓ Type checking passed"
else
    log "✗ Type checking failed"
    exit 1
fi

# Run unit tests (no infrastructure required)
log "Running unit tests..."
if pytest tests/unit/ -v; then
    log "✓ Unit tests passed"
else
    log "✗ Unit tests failed"
    exit 1
fi

# Run contract tests — schema drift gate (PR-14)
log "Running contract tests (schema drift gate)..."
if pytest tests/contract/ -v; then
    log "✓ Contract tests passed"
else
    log "✗ Contract tests failed"
    exit 1
fi

# Run chaos engineering tests
log "Running chaos engineering tests..."
if pytest tests/chaos/ -m chaos -v; then
    log "✓ Chaos engineering tests passed"
else
    log "✗ Chaos engineering tests failed"
    exit 1
fi

# Run integration tests (requires infrastructure)
log "Checking if infrastructure is available..."
if docker ps | grep -q "postgres\|qdrant\|redis"; then
    log "Infrastructure detected, running integration tests..."
    if pytest tests/integration/ -v --tb=short; then
        log "✓ Integration tests passed"
    else
        log "✗ Integration tests failed"
        exit 1
    fi
    
    # Run slow contract tests (roundtrip verification)
    log "Running slow contract tests (roundtrip verification)..."
    if pytest tests/contract/ -m slow -v --tb=short; then
        log "✓ Slow contract tests passed"
    else
        log "✗ Slow contract tests failed"
        exit 1
    fi
else
    log "Infrastructure not available, skipping integration tests"
fi

# Update timestamp
update_timestamp

log "Daily Operations of Done (DOD) check completed successfully"
log "All checks passed - system is ready for production"