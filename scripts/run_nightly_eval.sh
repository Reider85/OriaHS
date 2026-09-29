#!/bin/bash
# Nightly evaluation wrapper (C-07).
#
# Runs the nightly eval job and exits with code 1 if regressions are detected,
# blocking the release. This should be called from cron (e.g., "0 2 * * *")
# or CI pipelines as a gate.
#
#   ./scripts/run_nightly_eval.sh            # в текущем venv (uv run)
#   ./scripts/run_nightly_eval.sh --docker   # one-shot job в compose (профиль nightly)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# .env НЕ экспортируется вручную: pydantic-settings читает его сам
# (SettingsConfigDict(env_file=".env") в app/config.py). Ручной export ломался
# на значениях с пробелами и кавычками.
USE_DOCKER=0
for arg in "$@"; do
    case "$arg" in
        --docker) USE_DOCKER=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

echo "Starting nightly evaluation..."

if [ "$USE_DOCKER" = "1" ]; then
    exec docker compose --project-directory "$PROJECT_ROOT" -f "$PROJECT_ROOT/docker-compose.yml" \
        --profile nightly run --rm nightly-eval
fi

# `python -m app.eval` (а не app.eval.nightly): единая точка входа, та же,
# что и у compose-сервиса nightly-eval.
if command -v uv > /dev/null 2>&1; then
    exec uv run python -m app.eval
fi
exec python -m app.eval
