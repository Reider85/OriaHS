#!/bin/bash

# MVP Definition of Done (DoD) Validation Script (P-17)
# 
# This script runs automated validation of all 6 DoD criteria from ROADMAP §3.4
# and performs NFR performance tests. It provides pass/fail reporting with exit codes.
#
# Usage: ./scripts/run_dod_check.sh [options]
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

set -euo pipefail

# Default settings
VERBOSE=false
TIMEOUT_SECONDS=300
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    local status="$1"
    local message="$2"
    case "$status" in
        "PASS") echo -e "${GREEN}[PASS]${NC} $message" ;;
        "FAIL") echo -e "${RED}[FAIL]${NC} $message" ;;
        "WARN") echo -e "${YELLOW}[WARN]${NC} $message" ;;
        "INFO") echo -e "${BLUE}[INFO]${NC} $message" ;;
    esac
}

# Function to print verbose output
verbose() {
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${BLUE}[VERBOSE]${NC} $1"
    fi
}

# Function to show help
show_help() {
    cat << EOF
MVP Definition of Done (DoD) Validation Script (P-17)

This script runs automated validation of all 6 DoD criteria from ROADMAP §3.4
and performs NFR performance tests. It provides pass/fail reporting with exit codes.

Usage: $0 [options]

Options:
  -v, --verbose     Enable verbose output
  -t, --timeout N  Set timeout in seconds (default: 300)
  -h, --help        Show this help message

Exit codes:
  0 - All tests passed
  1 - One or more DoD criteria failed
  2 - Performance tests failed
  3 - Script error
EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -t|--timeout)
            TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 3
            ;;
    esac
done

# Check if we're in the right directory
if [[ ! -f "$PROJECT_ROOT/pyproject.toml" ]]; then
    print_status "FAIL" "Not in project root directory. Run this script from the project root."
    exit 3
fi

# Check if dependencies are installed
check_dependencies() {
    verbose "Checking dependencies..."
    
    if ! command -v python3 &> /dev/null; then
        print_status "FAIL" "Python3 is not installed"
        exit 3
    fi
    
    if ! command -v pytest &> /dev/null; then
        print_status "FAIL" "pytest is not installed"
        exit 3
    fi
    
    if ! command -v docker &> /dev/null; then
        print_status "WARN" "Docker is not installed. Some tests may fail."
    fi
    
    verbose "Dependencies check completed"
}

# Check if Docker Compose services are running
check_docker_services() {
    verbose "Checking Docker Compose services..."
    
    if ! command -v docker-compose &> /dev/null && ! command -v docker &> /dev/null; then
        print_status "WARN" "Docker Compose not available. Running tests without containers."
        return 0
    fi
    
    # Check if docker-compose.yml exists
    if [[ ! -f "$PROJECT_ROOT/docker-compose.yml" ]]; then
        print_status "WARN" "docker-compose.yml not found. Running tests without containers."
        return 0
    fi
    
    # Check if services are running
    if docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps | grep -q "Up"; then
        print_status "INFO" "Docker Compose services are running"
        return 0
    else
        print_status "WARN" "Docker Compose services are not running. Tests may fail."
        return 0
    fi
}

# Run DoD tests
run_dod_tests() {
    verbose "Running DoD tests..."
    
    local start_time=$(date +%s)
    local test_results=()
    local passed_tests=0
    local failed_tests=0
    
    # List of DoD tests to run
    local dod_tests=(
        "test_dod_1_index_search_workflow"
        "test_dod_2_rrf_fusion_deduplication"
        "test_dod_3_reconciler_recovery"
        "test_dod_4_metrics_dashboards"
        "test_dod_5_dead_letter_digest"
        "test_dod_6_fast_path_duplicate_content"
        "test_dod_end_to_end_workflow"
    )
    
    print_status "INFO" "Running DoD tests (timeout: $TIMEOUT_SECONDS seconds)..."
    
    # Run tests with pytest
    cd "$PROJECT_ROOT"
    
    for test in "${dod_tests[@]}"; do
        print_status "INFO" "Running $test..."
        
        if timeout $TIMEOUT_SECONDS python -m pytest tests/e2e/test_mvp_dod.py::$test -v --tb=short; then
            print_status "PASS" "$test passed"
            ((passed_tests++))
            test_results+=("$test:PASS")
        else
            local exit_code=$?
            if [[ $exit_code -eq 124 ]]; then
                print_status "FAIL" "$test timed out after $TIMEOUT_SECONDS seconds"
            else
                print_status "FAIL" "$test failed with exit code $exit_code"
            fi
            ((failed_tests++))
            test_results+=("$test:FAIL")
        fi
    done
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    # Print test summary
    print_status "INFO" "DoD Tests Summary:"
    print_status "INFO" "  Passed: $passed_tests"
    print_status "INFO" "  Failed: $failed_tests"
    print_status "INFO" "  Total Time: $duration seconds"
    
    # Return failure if any tests failed
    if [[ $failed_tests -gt 0 ]]; then
        print_status "FAIL" "$failed_tests DoD tests failed"
        return 1
    else
        print_status "PASS" "All DoD tests passed"
        return 0
    fi
}

# Run performance tests
run_performance_tests() {
    verbose "Running performance tests..."
    
    print_status "INFO" "Running NFR performance tests..."
    
    local start_time=$(date +%s)
    local test_passed=true
    
    # Test 1: Latency test (p99 <= 200ms)
    print_status "INFO" "Testing search latency (p99 <= 200ms)..."
    
    # Generate some test data first
    python3 -c "
import asyncio
import httpx
from uuid import uuid4

async def generate_test_data():
    async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
        for i in range(10):
            data = {
                'tenant_id': str(uuid4()),
                'external_ref': f'perf-doc-{i}',
                'title': f'Performance Test Document {i}',
                'content': 'This is a test document for performance testing.',
                'tags': ['performance'],
                'attributes': {}
            }
            await client.post('/index', json=data)
            await asyncio.sleep(0.1)

asyncio.run(generate_test_data())
" || {
        print_status "WARN" "Failed to generate test data for performance tests"
    }
    
    # Run latency test
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_latency():
    search_times = []
    async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
        for i in range(20):
            data = {
                'tenant_id': str(uuid4()),
                'query': 'performance test',
                'top_k': 10,
                'timeout_ms': 2000
            }
            start = time.time()
            try:
                response = await client.post('/search', json=data)
                latency = (time.time() - start) * 1000
                search_times.append(latency)
            except Exception as e:
                print(f'Search request failed: {e}')
                return False
    
    if not search_times:
        print('No successful search requests')
        return False
    
    p99 = sorted(search_times)[int(len(search_times) * 0.99)]
    print(f'P99 latency: {p99:.2f}ms')
    
    if p99 <= 200:
        print('Latency test passed')
        return True
    else:
        print(f'Latency test failed: P99 {p99:.2f}ms > 200ms')
        return False

success = asyncio.run(test_latency())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: P99 latency > 200ms"
        test_passed=false
    }
    
    # Test 2: Throughput test (>= 100 RPS)
    print_status "INFO" "Testing throughput (>= 100 RPS)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_throughput():
    requests = []
    start_time = time.time()
    
    async def make_request():
        data = {
            'tenant_id': str(uuid4()),
            'external_ref': f'throughput-doc-{int(time.time()*1000)}',
            'title': 'Throughput Test',
            'content': 'Test content for throughput.',
            'tags': ['throughput'],
            'attributes': {}
        }
        async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
            await client.post('/index', json=data)
    
    # Make 100 requests
    tasks = [make_request() for _ in range(100)]
    await asyncio.gather(*tasks)
    
    duration = time.time() - start_time
    rps = 100 / duration
    
    print(f'Throughput: {rps:.2f} RPS')
    
    if rps >= 100:
        print('Throughput test passed')
        return True
    else:
        print(f'Throughput test failed: {rps:.2f} RPS < 100 RPS')
        return False

success = asyncio.run(test_throughput())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Throughput < 100 RPS"
        test_passed=false
    }
    
    if [[ "$test_passed" == true ]]; then
        print_status "PASS" "All performance tests passed"
        return 0
    else
        print_status "FAIL" "One or more performance tests failed"
        return 2
    fi
}

# Main execution
main() {
    print_status "INFO" "Starting MVP DoD validation (P-17)..."
    print_status "INFO" "Project root: $PROJECT_ROOT"
    print_status "INFO" "Timeout: $TIMEOUT_SECONDS seconds"
    
    # Check dependencies
    check_dependencies
    
    # Check Docker services
    check_docker_services
    
    # Run DoD tests
    local dod_result=0
    if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
        # Function is being sourced, don't run tests automatically
        print_status "INFO" "DoD validation script loaded. Use run_dod_tests() to run tests."
    else
        # Script is being executed directly
        dod_result=0
        if [[ "$TIMEOUT_SECONDS" -gt 0 ]]; then
            run_dod_tests || dod_result=1
        fi
        
        # Run performance tests (only if DoD tests passed)
        if [[ $dod_result -eq 0 ]]; then
            run_performance_tests || dod_result=2
        fi
        
        # Final result
        if [[ $dod_result -eq 0 ]]; then
            print_status "PASS" "All MVP DoD criteria validated successfully!"
            exit 0
        else
            print_status "FAIL" "MVP DoD validation failed!"
            exit $dod_result
        fi
    fi
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi