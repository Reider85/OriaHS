<#
.SYNOPSIS
    OriaHS Infrastructure Startup Script

.DESCRIPTION
    Starts all infrastructure services (Docker Compose), runs database migrations,
    and launches the FastAPI application server.

.PARAMETER NoApp
    Start only Docker services, skip API server

.PARAMETER NoMigrate
    Skip database migrations

.PARAMETER NoSync
    Skip uv sync (dependencies install)

.PARAMETER Verbose
    Enable verbose output

.EXAMPLE
    .\scripts\start-infra.ps1
    .\scripts\start-infra.ps1 -NoApp
    .\scripts\start-infra.ps1 -NoMigrate -NoSync
#>

[CmdletBinding()]
param(
    [switch]$NoApp,
    [switch]$NoMigrate,
    [switch]$NoSync,
    [switch]$VerboseOutput
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PidFile = Join-Path $ScriptDir ".api-server.pid"
$LogDir = $ScriptDir
$LogFile = Join-Path $ScriptDir ".api-server.log"

$HealthTimeout = 60
$HealthInterval = 2
$ApiPort = if ($env:API_PORT) { $env:API_PORT } else { "8000" }

function Write-Status {
    param(
        [string]$Status,
        [string]$Message
    )
    switch ($Status) {
        "OK"   { Write-Host "[OK]   $Message" -ForegroundColor Green }
        "FAIL" { Write-Host "[FAIL] $Message" -ForegroundColor Red }
        "WARN" { Write-Host "[WARN] $Message" -ForegroundColor Yellow }
        "INFO" { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
        "WAIT" { Write-Host "[WAIT] $Message" -ForegroundColor DarkCyan }
    }
}

# Read value from .env file
function Get-EnvValue {
    param([string]$Key, [string]$Fallback = "")
    if (-not (Test-Path "$ProjectRoot\.env")) { return $Fallback }
    $line = Get-Content "$ProjectRoot\.env" | Where-Object { $_ -match "^\s*$([regex]::Escape($Key))=" } | Select-Object -Last 1
    if (-not $line) { return $Fallback }
    $value = ($line -split "=", 2)[1].Trim()
    if ($value) { return $value }
    return $Fallback
}

function Test-Prerequisites {
    Write-Verbose "Running pre-flight checks..."

    if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
        Write-Status "FAIL" "Not in project root. pyproject.toml not found."
        exit 1
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Status "FAIL" "Docker is not installed."
        exit 1
    }

    try { docker info 2>$null | Out-Null } catch {
        Write-Status "FAIL" "Docker daemon is not running."
        exit 1
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Status "FAIL" "uv is not installed. Install: pip install uv"
        exit 1
    }

    Write-Verbose "Pre-flight checks passed"
}

function Initialize-Env {
    $envPath = Join-Path $ProjectRoot ".env"
    $envExamplePath = Join-Path $ProjectRoot ".env.example"

    if (-not (Test-Path $envPath)) {
        if (Test-Path $envExamplePath) {
            Copy-Item $envExamplePath $envPath
            Write-Status "OK" "Created .env from .env.example"
        } else {
            Write-Status "WARN" "No .env or .env.example found. Using defaults."
        }
    } else {
        Write-Verbose ".env already exists"
    }
}

function Start-Infrastructure {
    Write-Status "INFO" "Building application image..."

    Push-Location $ProjectRoot
    try {
        # Build the image first to avoid pull issues
        docker compose build migrate
        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Failed to build application image"
            exit 1
        }
        Write-Status "OK" "Application image built"
    } finally {
        Pop-Location
    }
}

function Start-DockerServices {
    Write-Status "INFO" "Starting Docker Compose services..."

    Push-Location $ProjectRoot
    try {
        # Start only infrastructure services (exclude one-shot jobs like migrate/init-qdrant)
        $services = @("postgres", "qdrant", "redis", "prometheus", "grafana")
        docker compose up -d @services
        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Failed to start Docker Compose services"
            exit 1
        }
        Write-Status "OK" "Docker Compose services started: $($services -join ', ')"
    } finally {
        Pop-Location
    }
}

function Create-PgExtensions {
    Write-Status "INFO" "Creating PostgreSQL extensions..."
    
    Push-Location $ProjectRoot
    try {
        # vector extension is not in any migration (001 only creates pg_trgm/pgcrypto)
        docker compose exec -T postgres psql -U postgres -d orlahs `
            -c "CREATE EXTENSION IF NOT EXISTS vector;" `
            -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" `
            -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Failed to create PostgreSQL extensions"
            exit 1
        }
        Write-Status "OK" "PostgreSQL extensions created"
    } finally {
        Pop-Location
    }
}

function Wait-ForHealthy {
    Write-Status "WAIT" "Waiting for services to be healthy (timeout: ${HealthTimeout}s)..."

    $services = @("oriahs-postgres", "oriahs-qdrant", "oriahs-redis", "oriahs-prometheus", "oriahs-grafana")
    $elapsed = 0

    while ($elapsed -lt $HealthTimeout) {
        $allHealthy = $true

        foreach ($svc in $services) {
            try {
                $health = docker inspect --format='{{.State.Health.Status}}' $svc 2>$null
                if ($health -ne "healthy" -and $health -ne "running") {
                    $allHealthy = $false
                    break
                }
            } catch {
                $allHealthy = $false
                break
            }
        }

        if ($allHealthy) {
            Write-Host ""
            Write-Status "OK" "All services are healthy (${elapsed}s)"
            return
        }

        Start-Sleep -Seconds $HealthInterval
        $elapsed += $HealthInterval
        Write-Host "`r[WAIT]   Waiting... ${elapsed}s / ${HealthTimeout}s" -NoNewline -ForegroundColor DarkCyan
    }

    Write-Host ""
    Write-Status "FAIL" "Services did not become healthy within ${HealthTimeout}s"
    Push-Location $ProjectRoot
    docker compose ps
    Pop-Location
    exit 1
}

function Install-Dependencies {
    if ($NoSync) {
        Write-Verbose "Skipping uv sync"
        return
    }

    Write-Status "INFO" "Installing Python dependencies..."

    Push-Location $ProjectRoot
    try {
        uv sync
        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Failed to install dependencies"
            exit 1
        }
        Write-Status "OK" "Dependencies installed"
    } finally {
        Pop-Location
    }
}

function Invoke-Migrations {
    if ($NoMigrate) {
        Write-Verbose "Skipping migrations"
        return
    }

    Write-Status "INFO" "Running database migrations..."

    Push-Location $ProjectRoot
    try {
        docker compose run --rm migrate
        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Migrations failed"
            exit 1
        }
        Write-Status "OK" "Migrations applied"
    } finally {
        Pop-Location
    }
}

function Initialize-Qdrant {
    Write-Status "INFO" "Initializing Qdrant collection..."

    Push-Location $ProjectRoot
    try {
        docker compose run --rm init-qdrant
        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Qdrant initialization failed"
            exit 1
        }
        Write-Status "OK" "Qdrant collection created"
    } finally {
        Pop-Location
    }
}

function Start-AppServer {
    if ($NoApp) {
        Write-Verbose "Skipping API server start"
        return
    }

    # Stop existing server if running
    if (Test-Path $PidFile) {
        $oldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($oldPid) {
            try {
                $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Status "WARN" "Stopping existing API server (PID: $oldPid)..."
                    Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 2
                }
            } catch { }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    Write-Status "INFO" "Starting API server on port ${ApiPort}..."

    Push-Location $ProjectRoot
    try {
        $process = Start-Process -FilePath "uv" `
            -ArgumentList "run", "uvicorn", "app.main:app", "--reload", "--port", $ApiPort `
            -WindowStyle Hidden `
            -RedirectStandardOutput $LogFile `
            -PassThru

        $process.Id | Out-File -FilePath $PidFile -NoNewline

        Start-Sleep -Seconds 2

        $proc = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Status "OK" "API server started (PID: $($process.Id), log: $LogFile)"
        } else {
            Write-Status "FAIL" "API server failed to start. Check $LogFile"
            exit 1
        }
    } finally {
        Pop-Location
    }
}

function Show-Summary {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  OriaHS Infrastructure is running!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    
    $pgPort = Get-EnvValue "POSTGRES_PORT" "5433"
    $qdrantPort = Get-EnvValue "QDRANT_PORT" "6333"
    $redisPort = Get-EnvValue "REDIS_PORT" "6379"
    $grafanaPort = Get-EnvValue "GRAFANA_PORT" "3000"
    
    Write-Host "  PostgreSQL : " -NoNewline; Write-Host "localhost:$pgPort" -ForegroundColor Cyan
    Write-Host "  Qdrant     : " -NoNewline; Write-Host "localhost:$qdrantPort" -ForegroundColor Cyan
    Write-Host "  Redis      : " -NoNewline; Write-Host "localhost:$redisPort" -ForegroundColor Cyan
    Write-Host "  Grafana    : " -NoNewline; Write-Host "localhost:$grafanaPort" -ForegroundColor Cyan
    if (-not $NoApp) {
        Write-Host "  API Server : " -NoNewline; Write-Host "localhost:${ApiPort}" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host "  Stop with: " -NoNewline; Write-Host ".\scripts\stop-infra.ps1" -ForegroundColor Yellow
    Write-Host ""
}

# Main
Write-Status "INFO" "OriaHS Infrastructure Startup"
Write-Status "INFO" "Project root: $ProjectRoot"
Write-Host ""

Test-Prerequisites
Initialize-Env
Start-Infrastructure
Start-DockerServices
Wait-ForHealthy
Create-PgExtensions
Invoke-Migrations
Initialize-Qdrant
Install-Dependencies
Start-AppServer
Show-Summary
