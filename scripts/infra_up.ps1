<#
.SYNOPSIS
    Bring up the OriaHS stack (MVP infra + CRITICAL app services).

.DESCRIPTION
    Idempotent: safe to re-run. Mirrors scripts/infra_up.sh step for step.

    1. .env из .env.example (если отсутствует)
    2. docker compose build + up -d (инфраструктура + профиль app)
    3. ожидание healthcheck'ов
    4. CREATE EXTENSION IF NOT EXISTS vector   (её нет ни в одной миграции)
    5. alembic upgrade head                    (включая 005_eval_datasets, C-07)
    6. scripts\init_qdrant.py                  (коллекция documents, P-04)
    7. -SeedEval: scripts\seed_eval_corpus.py (C-07) + ожидание outbox
    8. GET /health/ready с ретраями + сводка URL

.EXAMPLE
    .\scripts\infra_up.ps1
    .\scripts\infra_up.ps1 -NoApp -SeedEval
    .\scripts\infra_up.ps1 -Rebuild -Nightly
#>
[CmdletBinding()]
param(
    [switch]$NoApp,
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$SkipMigrations,
    [switch]$SkipInitQdrant,
    [switch]$SkipExtensions,
    [switch]$SeedEval,
    [switch]$Nightly,
    [switch]$ShowCommands,
    [int]$TimeoutSeconds = 240,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$script:WithApp = -not $NoApp
$ScriptDir = Split-Path $PSCommandPath -Parent
$script:ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ComposeFile = Join-Path $script:ProjectRoot "docker-compose.yml"
$EnvFile = Join-Path $script:ProjectRoot ".env"
$EnvExample = Join-Path $script:ProjectRoot ".env.example"

$Red = "Red"; $Green = "Green"; $Yellow = "Yellow"; $Blue = "Cyan"

function Write-Status {
    param([ValidateSet("PASS", "FAIL", "WARN", "INFO", "STEP")][string]$Status, [string]$Message)
    $color = switch ($Status) {
        "PASS" { $Green }
        "FAIL" { $Red }
        "WARN" { $Yellow }
        default { $Blue }
    }
    Write-Host "[$Status] $Message" -ForegroundColor $color
}

function Write-Command {
    param([string]$Message)
    if ($ShowCommands) { Write-Host "[CMD] $Message" -ForegroundColor DarkGray }
}

# Обёртка над docker: PowerShell 5.1 превращает stderr нативной команды в
# terminating NativeCommandError при ErrorActionPreference=Stop, поэтому
# предпочтение временно снимается, а stderr склеивается с stdout.
function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
    Write-Command "docker $($DockerArgs -join ' ')"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & docker @DockerArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ Code = $code; Output = @($output) }
}

# Вызывает docker compose с проектными аргументами; возвращает exit code,
# вывод docker печатается в консоль.
function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    $all = @("compose", "--project-directory", $script:ProjectRoot, "-f", $ComposeFile)
    if ($script:WithApp) { $all += @("--profile", "app") }
    $result = Invoke-Docker @all @ComposeArgs
    foreach ($line in $result.Output) { Write-Host $line }
    return $result.Code
}

function Invoke-ComposeRaw {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    $all = @("compose", "--project-directory", $script:ProjectRoot, "-f", $ComposeFile)
    $all += $ComposeArgs
    return Invoke-Docker @all
}

# Значение переменной из .env (без «питоновских» хрупких конструкций).
function Get-EnvValue {
    param([string]$Key, [string]$Fallback = "")
    if (-not (Test-Path $EnvFile)) { return $Fallback }
    $line = Get-Content $EnvFile |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Key))=" } |
        Select-Object -Last 1
    if (-not $line) { return $Fallback }
    $value = ($line -split "=", 2)[1].Trim()
    if ($value) { return $value }
    return $Fallback
}

function Show-Usage {
    Write-Host @"
Bring up the OriaHS stack (MVP infra + CRITICAL app services).

Idempotent: safe to re-run. Steps:
  1. .env из .env.example (если отсутствует)
  2. docker compose build + up -d (инфраструктура + профиль app)
  3. ожидание healthcheck'ов
  4. CREATE EXTENSION IF NOT EXISTS vector (её нет ни в одной миграции)
  5. alembic upgrade head (включая 005_eval_datasets, C-07)
  6. scripts\init_qdrant.py (коллекция documents, P-04)
  7. -SeedEval: scripts\seed_eval_corpus.py (C-07) + ожидание outbox
  8. GET /health/ready с ретраями + сводка URL

Usage: .\scripts\infra_up.ps1 [options]
Options:
  -NoApp              только инфраструктура (postgres, qdrant, redis, prometheus, grafana)
  -NoBuild            не пересобирать образ
  -Rebuild            принудительная пересборка образа (без кэша)
  -SkipMigrations     не запускать alembic upgrade head
  -SkipInitQdrant     не создавать коллекцию Qdrant
  -SkipExtensions     не создавать PG-расширения
  -SeedEval           заполнить БД eval-корпусом (C-07) и дождаться outbox
  -Nightly            сразу прогнать ночной оффлайн-eval (C-07)
  -ShowCommands       печатать вызываемые docker-команды
  -TimeoutSeconds N   таймаут ожидания healthcheck'ов (default: 240)
  -Help               справка

Exit codes: 0 - стек поднят, 1 - ошибка шага, 2 - окружение не готово
"@
}

if ($Help) { Show-Usage; exit 0 }

# --- 1. preflight ------------------------------------------------------------
function Invoke-Preflight {
    Write-Status "STEP" "Preflight"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Status "FAIL" "docker is not installed (https://docs.docker.com/get-docker/)"
        exit 2
    }
    if ((Invoke-Docker "compose" "version").Code -ne 0) {
        Write-Status "FAIL" "docker compose v2 is not available (the v1 'docker-compose' binary is not supported)"
        exit 2
    }
    if ((Invoke-Docker "info").Code -ne 0) {
        Write-Status "FAIL" "docker daemon is not running"
        exit 2
    }
    $version = ((Invoke-Docker "--version").Output) -join ""
    $composeVersion = (((Invoke-Docker "compose" "version" "--short").Output) -join "").Trim()
    Write-Status "PASS" "docker: $version, $composeVersion"

    if (-not (Test-Path $EnvFile)) {
        if (Test-Path $EnvExample) {
            Copy-Item $EnvExample $EnvFile
            Write-Status "PASS" "created .env from .env.example"
        } else {
            Write-Status "FAIL" "neither .env nor .env.example found"
            exit 2
        }
    } else {
        Write-Status "PASS" ".env present"
    }

    # compose 2.24+ - иначе не понимает `env_file: [{path: .env, required: false}]`
    if ($composeVersion -match '^v?(\d+)\.(\d+)') {
        $minor = [int]$Matches[1] * 100 + [int]$Matches[2]
        if ($minor -lt 224) {
            Write-Status "WARN" "docker compose < 2.24: long-form env_file may be rejected; upgrade if 'config' fails"
        }
    }
}

# --- 2. build + up -----------------------------------------------------------
function Start-Containers {
    Write-Status "STEP" "Starting containers"

    if ($script:WithApp -and -not $NoBuild) {
        if ($Rebuild) {
            Write-Status "INFO" "Rebuilding application image (-Rebuild)..."
            if ((Invoke-Compose "build" "--pull" "--no-cache" "migrate") -ne 0) { exit 1 }
        } else {
            Write-Status "INFO" "Building application image..."
            if ((Invoke-Compose "build" "migrate") -ne 0) { exit 1 }
        }
    }

    $services = @("postgres", "qdrant", "redis", "prometheus", "grafana")
    if ($script:WithApp) {
        $services += @("api", "reconciler", "digest")
    } else {
        Write-Status "INFO" "-NoApp: skipping api, reconciler, digest"
    }

    # one-shot jobs (migrate / init-qdrant) исключены намеренно: они
    # запускаются ниже отдельными шагами, чтобы их вывод был виден.
    $upArgs = @("up", "-d", "--remove-orphans") + $services
    if ((Invoke-Compose @upArgs) -ne 0) { exit 1 }
    Write-Status "PASS" "containers started: $($services -join ', ')"
}

# --- 3. healthcheck wait -----------------------------------------------------
function Wait-ForServices {
    Write-Status "STEP" "Waiting for healthchecks (timeout ${TimeoutSeconds}s)"

    $services = @("postgres", "qdrant", "redis", "prometheus", "grafana")
    if ($script:WithApp) { $services += "api" }

    $pending = $services
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $pending = @()
        foreach ($service in $services) {
            $cid = (((Invoke-ComposeRaw "ps" "-q" $service).Output) -join "").Trim()
            if (-not $cid) { $pending += $service; continue }
            $state = (((Invoke-Docker "inspect" "-f" '{{.State.Status}}{{if .State.Health}}{{.State.Health.Status}}{{end}}' $cid).Output) -join "").Trim()
            if ($state -ne "runninghealthy" -and $state -ne "running") { $pending += $service }
        }

        if ($pending.Count -eq 0) {
            Write-Status "PASS" "all services healthy: $($services -join ', ')"
            return
        }
        if ($ShowCommands) { Write-Host "[WAIT] not ready yet: $($pending -join ', ')" -ForegroundColor DarkGray }
        Start-Sleep -Seconds 3
    }

    Write-Status "FAIL" "timeout ${TimeoutSeconds}s waiting for: $($pending -join ', ')"
    Write-Status "INFO" "logs: docker compose -f docker-compose.yml logs --tail=50 $($pending -join ' ')"
    exit 1
}

# --- 4. PG extensions --------------------------------------------------------
function New-PgExtensions {
    Write-Status "STEP" "PostgreSQL extensions"
    if ($SkipExtensions) { Write-Status "INFO" "-SkipExtensions"; return }

    $pgUser = Get-EnvValue "POSTGRES_USER" "postgres"
    $pgDb = Get-EnvValue "POSTGRES_DB" "orlahs"

    # vector нет ни в одной миграции (001 создаёт только pg_trgm/pgcrypto).
    $result = Invoke-ComposeRaw @(
        "exec", "-T", "postgres",
        "psql", "-v", "ON_ERROR_STOP=1", "-U", $pgUser, "-d", $pgDb,
        "-c", "CREATE EXTENSION IF NOT EXISTS vector;",
        "-c", "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
        "-c", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
    )
    if ($result.Code -eq 0) {
        Write-Status "PASS" "vector, pg_trgm, pgcrypto available"
    } else {
        foreach ($line in $result.Output) { Write-Host $line }
        Write-Status "FAIL" "could not create PostgreSQL extensions"
        exit 1
    }
}

# --- 5. миграции -------------------------------------------------------------
function Invoke-Migrations {
    Write-Status "STEP" "Database migrations (alembic upgrade head)"
    if ($SkipMigrations) { Write-Status "INFO" "-SkipMigrations"; return }
    if ((Invoke-Compose "run" "--rm" "migrate") -ne 0) {
        Write-Status "FAIL" "alembic upgrade head failed"
        exit 1
    }
    Write-Status "PASS" "schema is at head"
}

# --- 6. коллекция Qdrant -----------------------------------------------------
function Initialize-Qdrant {
    Write-Status "STEP" "Qdrant collection (scripts\init_qdrant.py)"
    if ($SkipInitQdrant) { Write-Status "INFO" "-SkipInitQdrant"; return }
    if ((Invoke-Compose "run" "--rm" "init-qdrant") -ne 0) {
        Write-Status "FAIL" "init_qdrant.py failed - vector search will not work"
        exit 1
    }
    Write-Status "PASS" "collection matches qdrant_collections.yaml"
}

# --- 7. eval-корпус ----------------------------------------------------------
function Invoke-SeedEval {
    if (-not $SeedEval) { return }
    Write-Status "STEP" "Seeding eval corpus (C-07)"
    if ((Invoke-ComposeRaw "--profile", "tools", "run", "--rm", "seed-eval") -ne 0) {
        Write-Status "FAIL" "seed_eval_corpus.py failed"
        exit 1
    }
    Write-Status "PASS" "eval documents inserted"

    if (-not $script:WithApp) {
        Write-Status "WARN" "reconciler is not running (-NoApp): Qdrant stays empty until 'infra_up.ps1' without -NoApp"
        return
    }

    Write-Status "INFO" "waiting for the reconciler to drain the outbox (CPU embedding is slow)..."
    $pgUser = Get-EnvValue "POSTGRES_USER" "postgres"
    $pgDb = Get-EnvValue "POSTGRES_DB" "orlahs"
    $deadline = (Get-Date).AddSeconds(600)
    while ((Get-Date) -lt $deadline) {
        $pending = (Invoke-ComposeRaw @(
            "exec", "-T", "postgres", "psql", "-U", $pgUser, "-d", $pgDb, "-tAc",
            "SELECT count(*) FROM search_outbox WHERE status IN ('pending','failed','in_progress');"
        )).Output -join ""
        $pending = $pending.Trim()
        if ($pending -eq "0") {
            Write-Status "PASS" "outbox drained - Qdrant is in sync"
            return
        }
        if ($ShowCommands) { Write-Host "[WAIT] outbox pending: $pending" -ForegroundColor DarkGray }
        Start-Sleep -Seconds 5
    }
    Write-Status "WARN" "outbox not drained in 600s (pending rows: $pending)"
}

# --- 8. ночной eval ----------------------------------------------------------
function Invoke-NightlyEval {
    if (-not $Nightly) { return }
    Write-Status "STEP" "Nightly offline eval (C-07)"
    $exit = Invoke-ComposeRaw @("--profile", "nightly", "run", "--rm", "nightly-eval")
    if ($exit.Code -ne 0) {
        Write-Status "FAIL" "nightly eval reported a regression (or failed)"
        exit 1
    }
    Write-Status "PASS" "nightly eval passed, no regression"
}

# --- 9. readiness + сводка ---------------------------------------------------
function Test-Readiness {
    Write-Status "STEP" "Readiness"
    if (-not $script:WithApp) {
        Write-Status "INFO" "api not started (-NoApp), skipping /health/ready"
        return
    }

    $apiPort = Get-EnvValue "API_PORT" "8000"
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        try {
            $body = Invoke-RestMethod -Uri "http://localhost:$apiPort/health/ready" -TimeoutSec 5
            Write-Status "PASS" ("/health/ready: " + ($body | ConvertTo-Json -Compress -Depth 5))
            return
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    Write-Status "FAIL" "/health/ready did not answer on port $apiPort"
    Write-Status "INFO" "logs: docker compose -f docker-compose.yml logs --tail=50 api"
    exit 1
}

function Write-RerankerWarning {
    $mock = Get-EnvValue "RERANKER_MOCK_MODE" "false"
    $warmup = Get-EnvValue "RERANKER_WARMUP" "false"
    if ($mock -eq "true") {
        Write-Status "WARN" "RERANKER_MOCK_MODE=true: rerank returns RRF scores, not cross-encoder scores"
    } elseif ($warmup -ne "true") {
        Write-Status "WARN" "RERANKER_WARMUP=false: the first reranked search downloads BAAI/bge-reranker-v2-m3 (~2.3 GB) on the API container"
    }
}

function Write-Summary {
    $apiPort = Get-EnvValue "API_PORT" "8000"
    $pgPort = Get-EnvValue "POSTGRES_PORT" "5432"
    $qdrantPort = Get-EnvValue "QDRANT_PORT" "6333"
    $promPort = Get-EnvValue "PROMETHEUS_PORT" "9090"
    $grafanaPort = Get-EnvValue "GRAFANA_PORT" "3000"
    $grafanaUser = Get-EnvValue "GRAFANA_USER" "admin"

    Write-Host ""
    Write-Status "PASS" "stack is up"
    Write-Host "  API           http://localhost:$apiPort/docs        (/metrics, /health/live, /health/ready)"
    Write-Host "  Qdrant        http://localhost:$qdrantPort/dashboard"
    Write-Host "  Prometheus    http://localhost:$promPort            (targets: /api/v1/targets)"
    Write-Host "  Grafana       http://localhost:$grafanaPort          (user: $grafanaUser, password: `$GRAFANA_PASSWORD)"
    Write-Host "  PostgreSQL    localhost:$pgPort"
    Write-Host ""
    Write-Host "  Dashboards: 4 MVP + 4 CRITICAL (reranker, eval-regression, pushdown, circuit-breaker)"
    Write-Host "  Stop:       .\scripts\infra_down.ps1   (add -v to drop volumes)"
}

# --- main --------------------------------------------------------------------
Write-Status "INFO" "OriaHS infra up - project root: $script:ProjectRoot"
Invoke-Preflight
Start-Containers
Wait-ForServices
New-PgExtensions
Invoke-Migrations
Initialize-Qdrant
Invoke-SeedEval
Invoke-NightlyEval
Test-Readiness
Write-RerankerWarning
Write-Summary
