<#
.SYNOPSIS
    Stop the OriaHS stack, rolling the database schema back to base.

.DESCRIPTION
    Mirrors scripts/infra_down.sh. Порядок важен: alembic downgrade base
    выполняется ДО остановки контейнеров, иначе БД недоступна и откат
    невозможен. Данные при этом НЕ удаляются: том pgdata остаётся, чтобы
    infra_up.ps1 поднял стек на прежней схеме.

    1. alembic downgrade base  (001..005 откатываются, расширения остаются)
    2. docker compose down --remove-orphans (все профили)
    3. -DropVolumes: снос pgdata, qdrant_data, redis_data, prometheus_data,
       grafana_data, hf_cache
    4. -Purge: удаление локального образа приложения и build-кэша

.EXAMPLE
    .\scripts\infra_down.ps1
    .\scripts\infra_down.ps1 -DropVolumes
    .\scripts\infra_down.ps1 -SkipMigrations
#>
[CmdletBinding()]
param(
    [switch]$DropVolumes,
    [switch]$KeepVolumes,
    [switch]$Purge,
    [switch]$SkipMigrations,
    [switch]$NoRemoveOrphans,
    [switch]$ShowCommands,
    [int]$TimeoutSeconds = 30,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path $PSCommandPath -Parent
$script:ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ComposeFile = Join-Path $script:ProjectRoot "docker-compose.yml"
$EnvFile = Join-Path $script:ProjectRoot ".env"

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

# Обёртка над docker: PowerShell 5.1 превращает stderr нативной команды в
# terminating NativeCommandError при ErrorActionPreference=Stop.
function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
    if ($ShowCommands) { Write-Host "[CMD] docker $($DockerArgs -join ' ')" -ForegroundColor DarkGray }
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

# down/init-qdrant работают по всем профилям: api-сервисы и one-shot jobs
# (nightly/tools) не должны остаться висеть после остановки.
function Invoke-ComposeAll {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    $all = @(
        "compose", "--project-directory", $script:ProjectRoot, "-f", $ComposeFile,
        "--profile", "app", "--profile", "tools", "--profile", "nightly"
    )
    $result = Invoke-Docker ($all + $ComposeArgs)
    foreach ($line in $result.Output) { Write-Host $line }
    return $result.Code
}

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
Stop the OriaHS stack, rolling the database schema back to base.

alembic downgrade base runs BEFORE the containers are stopped (otherwise the
database is already gone). Data is preserved: the pgdata volume survives, so
infra_up.ps1 brings the stack back on the previous schema.

Steps:
  1. alembic downgrade base   (001..005 down; PG extensions stay by design)
  2. docker compose down --remove-orphans (all profiles)
  3. -DropVolumes: drop pgdata, qdrant_data, redis_data, prometheus_data,
     grafana_data, hf_cache
  4. -Purge: also remove the local application image and build cache

Usage: .\scripts\infra_down.ps1 [options]
Options:
  -DropVolumes        удалить тома (необратимо)
  -KeepVolumes        явно оставить тома (поведение по умолчанию)
  -Purge              удалить образ oriahs:* и build-кэш
  -SkipMigrations     не откатывать миграции (БД уже недоступна)
  -NoRemoveOrphans   не трогать контейнеры вне текущего compose-файла
  -ShowCommands       печатать вызываемые docker-команды
  -TimeoutSeconds N   таймаут остановки контейнеров (default: 30)
  -Help               справка

Exit codes: 0 - стек остановлен, 1 - ошибка остановки, 2 - docker недоступен
"@
}

if ($Help) { Show-Usage; exit 0 }

function Test-Preflight {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Status "FAIL" "docker is not installed"
        exit 2
    }
    if ((Invoke-Docker "compose" "version").Code -ne 0) {
        Write-Status "FAIL" "docker compose v2 is not available"
        exit 2
    }
    if ((Invoke-Docker "info").Code -ne 0) {
        Write-Status "FAIL" "docker daemon is not running"
        exit 2
    }
}

# Откат схемы. Предупреждение, а не ошибка: если БД уже остановлена, откат
# невозможен, но падать из-за этого нельзя - пользователь всё равно хочет
# погасить стек.
function Invoke-Downgrade {
    Write-Status "STEP" "Rolling the schema back (alembic downgrade base)"
    if ($SkipMigrations) { Write-Status "INFO" "-SkipMigrations"; return }

    $psArgs = @(
        "compose", "--project-directory", $script:ProjectRoot, "-f", $ComposeFile,
        "ps", "-q", "postgres"
    )
    $pgRunning = (((Invoke-Docker $psArgs).Output) -join "").Trim()
    if (-not $pgRunning) {
        Write-Status "WARN" "postgres container is not running - cannot downgrade; run 'alembic downgrade base' manually if the schema matters"
        return
    }

    $runArgs = @(
        "compose", "--project-directory", $script:ProjectRoot, "-f", $ComposeFile,
        "run", "--rm", "migrate", "alembic", "downgrade", "base"
    )
    $result = Invoke-Docker $runArgs
    foreach ($line in $result.Output) { Write-Host $line }
    if ($result.Code -eq 0) {
        Write-Status "PASS" "schema is at base (001..005 rolled back; PG extensions kept by design)"
    } else {
        Write-Status "WARN" "alembic downgrade base failed - continuing with shutdown"
        Write-Status "INFO" "logs: docker compose -f docker-compose.yml logs --tail=50 migrate"
    }
}

function Stop-Stack {
    Write-Status "STEP" "Stopping containers"
    $downArgs = @("down", "--timeout", "$TimeoutSeconds")
    if (-not $NoRemoveOrphans) { $downArgs += "--remove-orphans" }
    if ((Invoke-ComposeAll $downArgs) -ne 0) {
        Write-Status "FAIL" "docker compose down failed"
        exit 1
    }
    Write-Status "PASS" "containers stopped"
}

function Remove-Volumes {
    if (-not $DropVolumes -or $KeepVolumes) {
        Write-Status "INFO" "volumes kept (pgdata, qdrant_data, redis_data, prometheus_data, grafana_data, hf_cache)"
        return
    }
    Write-Status "STEP" "Removing volumes"
    $downArgs = @("down", "--volumes", "--timeout", "$TimeoutSeconds")
    if (-not $NoRemoveOrphans) { $downArgs += "--remove-orphans" }
    if ((Invoke-ComposeAll $downArgs) -ne 0) {
        Write-Status "FAIL" "could not remove volumes"
        exit 1
    }
    Write-Status "PASS" "volumes removed - data is gone"
}

function Remove-Images {
    if (-not $Purge) { return }
    Write-Status "STEP" "Removing images and build cache"

    $image = Get-EnvValue "APP_IMAGE" "oriahs:dev"
    if ((Invoke-Docker "image" "inspect" $image).Code -eq 0) {
        if ((Invoke-Docker "image" "rm" "-f" $image).Code -eq 0) {
            Write-Status "PASS" "image $image removed"
        } else {
            Write-Status "WARN" "could not remove image $image"
        }
    } else {
        Write-Status "INFO" "image $image not present locally"
    }
    if ((Invoke-Docker "builder" "prune" "-f").Code -eq 0) {
        Write-Status "PASS" "build cache pruned"
    } else {
        Write-Status "WARN" "could not prune build cache"
    }
}

function Write-Report {
    Write-Host ""
    Write-Status "PASS" "stack is down"
    Write-Host "  up again:  .\scripts\infra_up.ps1"
    Write-Host "  from zero: .\scripts\infra_up.ps1 -Rebuild   (после down -v)"
}

# --- main --------------------------------------------------------------------
Write-Status "INFO" "OriaHS infra down - project root: $script:ProjectRoot"
Test-Preflight
Invoke-Downgrade
Stop-Stack
Remove-Volumes
Remove-Images
Write-Report
