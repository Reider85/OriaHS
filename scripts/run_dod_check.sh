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
TIMEOUT_SECONDS=600  # Increased for SLA test
SLA_DURATION_SECONDS=600  # 10 minutes for SLA test
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
    
    # Print NFR results
    if [[ "$test_passed" == true ]]; then
        print_status "INFO" "NFR Performance Results:"
        print_status "INFO" "  Latency p99: ${p99_value:-N/A}ms (target: ≤200ms)"
        print_status "INFO" "  Index Throughput: ${throughput_value:-N/A} doc/min (target: ≥500 doc/min)"
        print_status "INFO" "  Search Throughput: ${rps_value:-N/A} RPS (target: ≥100 RPS)"
        print_status "INFO" "  Lag: ${lag_value:-N/A}s (target: ≤30s)"
        print_status "INFO" "  SLA: ${sla_value:-N/A} (target: ≥99% over ${SLA_DURATION_SECONDS}s)"
    fi
    
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
    
    # Test 1: Latency test (p99 <= 200ms, 1000 requests)
    print_status "INFO" "Testing search latency (p99 <= 200ms, 1000 requests)..."
    
    # Generate some test data first
    python3 -c "
import asyncio
import httpx
from uuid import uuid4

async def generate_test_data():
    async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
        for i in range(100):
            data = {
                'tenant_id': str(uuid4()),
                'external_ref': f'perf-doc-{i}',
                'title': f'Performance Test Document {i}',
                'content': 'This is a test document for performance testing.',
                'tags': ['performance'],
                'attributes': {}
            }
            await client.post('/index', json=data)
            await asyncio.sleep(0.01)

asyncio.run(generate_test_data())
" || {
        print_status "WARN" "Failed to generate test data for performance tests"
    }
    
    # Run latency test with 1000 requests
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_latency():
    search_times = []
    async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
        for i in range(1000):
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
                continue
    
    if not search_times:
        print('No successful search requests')
        return False, 0
    
    p99 = sorted(search_times)[int(len(search_times) * 0.99)]
    print(f'P99 latency: {p99:.2f}ms')
    
    if p99 <= 200:
        print('Latency test passed')
        return True, p99
    else:
        print(f'Latency test failed: P99 {p99:.2f}ms > 200ms')
        return False, p99

success, p99_value = asyncio.run(test_latency())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: P99 latency > 200ms"
        test_passed=false
    }
    
    # Test 2: Indexing throughput (≥ 500 doc/min)
    print_status "INFO" "Testing indexing throughput (≥ 500 doc/min)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_index_throughput():
    start_time = time.time()
    
    async def make_request():
        data = {
            'tenant_id': str(uuid4()),
            'external_ref': f'index-doc-{int(time.time()*1000)}',
            'title': 'Index Test Document',
            'content': 'This is test content for indexing throughput.',
            'tags': ['indexing'],
            'attributes': {}
        }
        async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
            await client.post('/index', json=data)
    
    # Make 500 requests
    tasks = [make_request() for _ in range(500)]
    await asyncio.gather(*tasks)
    
    duration = time.time() - start_time
    docs_per_min = 500 / (duration / 60)
    
    print(f'Indexing throughput: {docs_per_min:.1f} doc/min')
    
    if docs_per_min >= 500:
        print('Indexing throughput test passed')
        return True, docs_per_min
    else:
        print(f'Indexing throughput test failed: {docs_per_min:.1f} doc/min < 500 doc/min')
        return False, docs_per_min

success, throughput_value = asyncio.run(test_index_throughput())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Indexing throughput < 500 doc/min"
        test_passed=false
    }
    
    # Test 3: Search throughput (≥ 100 RPS)
    print_status "INFO" "Testing search throughput (≥ 100 RPS, 1000 requests)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_search_throughput():
    start_time = time.time()
    
    async def make_request():
        data = {
            'tenant_id': str(uuid4()),
            'query': 'test search throughput',
            'top_k': 10,
            'timeout_ms': 2000
        }
        async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
            await client.post('/search', json=data)
    
    # Make 1000 requests
    tasks = [make_request() for _ in range(1000)]
    await asyncio.gather(*tasks)
    
    duration = time.time() - start_time
    rps = 1000 / duration
    
    print(f'Search throughput: {rps:.1f} RPS')
    
    if rps >= 100:
        print('Search throughput test passed')
        return True, rps
    else:
        print(f'Search throughput test failed: {rps:.1f} RPS < 100 RPS')
        return False, rps

success, rps_value = asyncio.run(test_search_throughput())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Search throughput < 100 RPS"
        test_passed=false
    }
    
    # Test 4: Lag test (≤ 30s)
    print_status "INFO" "Testing index-to-search lag (≤ 30s)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_lag():
    tenant_id = str(uuid4())
    
    # Index a document
    data = {
        'tenant_id': tenant_id,
        'external_ref': 'lag-test-doc',
        'title': 'Lag Test Document',
        'content': 'This document tests the lag between index and search.',
        'tags': ['lag'],
        'attributes': {}
    }
    async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
        index_response = await client.post('/index', json=data)
        doc_id = index_response.json()['doc_id']
    
    # Wait for it to appear in search
    start_time = time.time()
    found = False
    
    while time.time() - start_time < 35:  # 35s timeout
        try:
            search_data = {
                'tenant_id': tenant_id,
                'query': 'lag test document',
                'top_k': 10,
                'timeout_ms': 2000
            }
            response = await client.post('/search', json=search_data)
            results = response.json()['hits']
            if any(hit['doc_id'] == doc_id for hit in results):
                found = True
                break
        except Exception:
            pass
        await asyncio.sleep(2)
    
    lag = time.time() - start_time if found else 35
    print(f'Lag: {lag:.1f}s')
    
    if found and lag <= 30:
        print('Lag test passed')
        return True, lag
    else:
        print(f'Lag test failed: Document not found or lag {lag:.1f}s > 30s')
        return False, lag

success, lag_value = asyncio.run(test_lag())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Lag > 30s or document not found"
        test_passed=false
    }
    
    # Test 5: SLA test (99% success rate over 10 minutes)
    print_status "INFO" "Testing SLA (99% success rate over ${SLA_DURATION_SECONDS}s)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_sla():
    total_requests = 0
    successful_requests = 0
    start_time = time.time()
    duration = ${SLA_DURATION_SECONDS}
    
    async def make_request():
        nonlocal total_requests, successful_requests
        total_requests += 1
        
        tenant_id = str(uuid4())
        data = {
            'tenant_id': tenant_id,
            'query': 'sla test',
            'top_k': 10,
            'timeout_ms': 2000
        }
        try:
            async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
                await client.post('/search', json=data)
            successful_requests += 1
        except Exception:
            pass
    
    # Run requests concurrently for the duration
    tasks = []
    while time.time() - start_time < duration:
        task = asyncio.create_task(make_request())
        tasks.append(task)
        await asyncio.sleep(0.1)  # Small delay to avoid overwhelming
    
    # Wait for remaining tasks
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    
    if total_requests == 0:
        success_rate = 0
    else:
        success_rate = successful_requests / total_requests
    
    print(f'SLA: {success_rate:.3f} ({successful_requests}/{total_requests})')
    
    if success_rate >= 0.99:
        print('SLA test passed')
        return True, success_rate
    else:
        print(f'SLA test failed: {success_rate:.3f} < 0.99')
        return False, success_rate

success, sla_value = asyncio.run(test_sla())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: SLA < 99%"
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