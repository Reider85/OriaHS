#!/bin/bash

# Chaos engineering weekly cron job for OriaHS
# Runs chaos tests and uploads reports

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/run_chaos_weekly.py"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/chaos_weekly_$(date +%Y%m%d_%H%M%S).log"

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

# Main execution
log "Starting chaos engineering weekly run"

# Check if Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    log "ERROR: Python script not found: $PYTHON_SCRIPT"
    exit 1
fi

# Activate virtual environment if it exists
if [ -f "$SCRIPT_DIR/../.venv/bin/activate" ]; then
    log "Activating virtual environment"
    source "$SCRIPT_DIR/../.venv/bin/activate"
fi

# Run chaos tests
log "Running chaos tests..."
cd "$SCRIPT_DIR/.."

if python "$PYTHON_SCRIPT"; then
    log "Chaos tests completed successfully"
    exit_code=0
else
    log "ERROR: Chaos tests failed"
    exit_code=1
fi

# Check if we're in CI/CD and need to upload artifacts
if [ -n "$CI" ]; then
    log "Uploading artifacts to CI/CD..."
    
    # Upload log file
    if [ -f "$LOG_FILE" ]; then
        # This would be implemented based on your CI/CD system
        # For GitHub Actions:
        # echo "::notice::Chaos test log available at $LOG_FILE"
        
        # For other systems, you might upload to S3, Artifactory, etc.
        log "Log file available at: $LOG_FILE"
    fi
    
    # Upload report files
    report_dir="chaos_reports"
    if [ -d "$report_dir" ]; then
        log "Chaos reports available in: $report_dir"
        # Upload logic would go here
    fi
fi

# Exit with appropriate code
log "Chaos weekly run completed with exit code: $exit_code"
exit $exit_code