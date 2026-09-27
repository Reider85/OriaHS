#!/bin/bash
# Nightly evaluation cron wrapper (C-07).
#
# Runs the nightly eval job and exits with code 1 if regressions are detected,
# blocking the release. This should be called from cron (e.g., "0 2 * * *")
# or CI pipelines as a gate.

set -euo pipefail

# Get project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Load environment
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Run nightly eval
echo "Starting nightly evaluation..."
python -m app.eval.nightly

# Exit with the same code as the eval job
exit $?