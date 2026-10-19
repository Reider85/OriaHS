<#
.SYNOPSIS
    OriaHS Infrastructure Shutdown Script

.DESCRIPTION
    Stops the API server and all Docker Compose services.

.PARAMETER KeepData
    Don't remove Docker volumes (preserve data)

.PARAMETER DockerOnly
    Stop only Docker services, leave API server running

.EXAMPLE
    .\scripts\stop-infra.ps1
    .\scripts\stop-infra.ps1 -KeepData
    .\scripts\stop-infra.ps1 -DockerOnly
#>

[CmdletBinding()]
param(
    [switch]$KeepData,
    [switch]$DockerOnly
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PidFile = Join-Path $ScriptDir ".api-server.pid"

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
    }
}

function Stop-AppServer {
    if ($DockerOnly) {
        Write-Verbose "Skipping API server stop (--docker-only)"
        return
    }

    if (-not (Test-Path $PidFile)) {
        Write-Verbose "No PID file found, API server may not be running"
        return
    }

    $pid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not $pid) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return
    }

    try {
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Status "INFO" "Stopping API server (PID: $pid)..."
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue

            $waited = 0
            while ($waited -lt 5) {
                $stillRunning = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if (-not $stillRunning) { break }
                Start-Sleep -Seconds 1
                $waited++
            }

            $stillRunning = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($stillRunning) {
                Write-Status "WARN" "Force killing API server..."
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            }

            Write-Status "OK" "API server stopped"
        } else {
            Write-Status "WARN" "API server (PID: $pid) is not running"
        }
    } catch {
        Write-Status "WARN" "Error stopping API server: $_"
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $ScriptDir ".api-server.log") -Force -ErrorAction SilentlyContinue
}

function Stop-Infrastructure {
    Write-Status "INFO" "Stopping Docker Compose services..."

    Push-Location $ProjectRoot
    try {
        if ($KeepData) {
            Write-Verbose "Preserving volumes (--keep-data)"
            docker compose down --volumes
        } else {
            docker compose down
        }

        if ($LASTEXITCODE -ne 0) {
            Write-Status "FAIL" "Failed to stop Docker Compose services"
            exit 1
        }
        Write-Status "OK" "Docker Compose services stopped"
    } finally {
        Pop-Location
    }
}

# Main
Write-Status "INFO" "OriaHS Infrastructure Shutdown"
Write-Host ""

Stop-AppServer
Stop-Infrastructure

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  OriaHS Infrastructure stopped." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Start with: " -NoNewline; Write-Host ".\scripts\start-infra.ps1" -ForegroundColor Yellow
Write-Host ""
