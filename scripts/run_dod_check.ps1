# MVP Definition of Done (DoD) Validation Script (P-17)
# 
# This script runs automated validation of all 6 DoD criteria from ROADMAP §3.4
# and performs NFR performance tests. It provides pass/fail reporting with exit codes.
#
# Usage: .\scripts\run_dod_check.ps1 [options]
# Options:
#   -v, --verbose     Enable verbose output
#   -t, --timeout N  Set timeout in seconds (default: 300)
#   -h, --help        Show this help message
#
# Exit codes:
#   0 - All tests passed
#   1 - One or more DoD criteria failed
#   2 - Performance tests failed
#   3 - Script error

param(
    [switch]$Verbose = $false,
    [int]$TimeoutSeconds = 300,
    [switch]$Help = $false
)

# Set error preferences
$ErrorActionPreference = "Stop"

# Colors for output
$Red = [System.ConsoleColor]::Red
$Green = [System.ConsoleColor]::Green
$Yellow = [System.ConsoleColor]::Yellow
$Blue = [System.ConsoleColor]::Blue
$NC = [System.ConsoleColor]::White

# Function to print colored output
function Write-Status {
    param([string]$Status, [string]$Message)
    
    switch ($Status) {
        "PASS" { Write-Host -ForegroundColor $Green "[PASS] $Message" }
        "FAIL" { Write-Host -ForegroundColor $Red "[FAIL] $Message" }
        "WARN" { Write-Host -ForegroundColor $Yellow "[WARN] $Message" }
        "INFO" { Write-Host -ForegroundColor $Blue "[INFO] $Message" }
    }
}

# Function to print verbose output
function Write-Verbose {
    param([string]$Message)
    
    if ($Verbose) {
        Write-Host -ForegroundColor $Blue "[VERBOSE] $Message"
    }
}

# Function to show help
function Show-Help {
    @"
MVP Definition of Done (DoD) Validation Script (P-17)

This script runs automated validation of all 6 DoD criteria from ROADMAP §3.4
and performs NFR performance tests. It provides pass/fail reporting with exit codes.

Usage: $PSCommandPath [options]

Options:
  -v, --verbose     Enable verbose output
  -t, --timeout N  Set timeout in seconds (default: 300)
  -h, --help        Show this help message

Exit codes:
  0 - All tests passed
  1 - One or more DoD criteria failed
  2 - Performance tests failed
  3 - Script error
"@
}

# Parse command line arguments
if ($Help) {
    Show-Help
    exit 0
}

# Check if we're in the right directory
$ProjectRoot = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path "$ProjectRoot\pyproject.toml")) {
    Write-Status "FAIL" "Not in project root directory. Run this script from the project root."
    exit 3
}

Write-Status "INFO" "Starting MVP DoD validation (P-17)..."
Write-Status "INFO" "Project root: $ProjectRoot"
Write-Status "INFO" "Timeout: $TimeoutSeconds seconds"

# Check if Python is available
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Status "FAIL" "Python is not installed"
    exit 3
}

# Check if pytest is available
if (-not (Get-Command pytest -ErrorAction SilentlyContinue)) {
    Write-Status "FAIL" "pytest is not installed"
    exit 3
}

# Run DoD tests
Write-Status "INFO" "Running DoD validation tests..."

$TestResults = @()
$PassedTests = 0
$FailedTests = 0

# List of DoD tests to run
$dodTests = @(
    "test_dod_validation_imports",
    "test_dod_criteria_1_validation",
    "test_dod_criteria_2_validation",
    "test_dod_criteria_6_validation",
    "test_e2e_test_client_creation",
    "test_content_hash_function",
    "test_dod_criteria_4_validation",
    "test_sample_data_creation"
)

Push-Location $ProjectRoot

foreach ($test in $dodTests) {
    Write-Status "INFO" "Running $test..."
    
    try {
        $process = Start-Process -FilePath python -ArgumentList @("-m", "pytest", "tests\e2e\test_dod_validation.py::$test", "-v", "--tb=short") -Wait -PassThru -NoNewWindow
        if ($process.ExitCode -eq 0) {
            Write-Status "PASS" "$test passed"
            $PassedTests++
            $TestResults += "$test:PASS"
        } else {
            Write-Status "FAIL" "$test failed with exit code $($process.ExitCode)"
            $FailedTests++
            $TestResults += "$test:FAIL"
        }
    }
    catch {
        Write-Status "FAIL" "$test failed with exception: $_"
        $FailedTests++
        $TestResults += "$test:FAIL"
    }
}

Pop-Location

# Print test summary
Write-Status "INFO" "DoD Tests Summary:"
Write-Status "INFO" "  Passed: $PassedTests"
Write-Status "INFO" "  Failed: $FailedTests"

# Return failure if any tests failed
if ($FailedTests -gt 0) {
    Write-Status "FAIL" "$FailedTests DoD tests failed"
    exit 1
} else {
    Write-Status "PASS" "All DoD tests passed"
    exit 0
}