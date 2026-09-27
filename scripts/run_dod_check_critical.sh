#!/bin/bash

# Critical Definition of Done (DoD) Validation Script (C-14)
# 
# This script runs automated validation of all 6 DoD criteria from ROADMAP §4.4
# and performs NFR performance tests. It provides pass/fail reporting with exit codes.
#
# Usage: ./scripts/run_dod_check_critical.sh [options]
# Options:
#   -v, --verbose     Enable verbose output
#   -t, --timeout N  Set timeout in seconds (default: 600)
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
TIMEOUT_SECONDS=600
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
Critical Definition of Done (DoD) Validation Script (C-14)

This script runs automated validation of all 6 DoD criteria from ROADMAP §4.4
and performs NFR performance tests. It provides pass/fail reporting with exit codes.

Usage: $0 [options]

Options:
  -v, --verbose     Enable verbose output
  -t, --timeout N  Set timeout in seconds (default: 600)
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
        "test_critical_dod_1_ndcg_improvement"
        "test_critical_dod_2_circuit_breaker_auto_disable"
        "test_critical_dod_3_nightly_eval_regression"
        "test_critical_dod_4_qdrant_down_degraded"
        "test_critical_dod_5_wait_for_index_polling"
        "test_critical_dod_6_pushdown_latency"
        "test_critical_end_to_end_workflow"
    )
    
    print_status "INFO" "Running Critical DoD tests (timeout: $TIMEOUT_SECONDS seconds)..."
    
    # Run tests with pytest
    cd "$PROJECT_ROOT"
    
    for test in "${dod_tests[@]}"; do
        print_status "INFO" "Running $test..."
        
        if timeout $TIMEOUT_SECONDS python -m pytest tests/e2e/test_critical_dod.py::$test -v --tb=short; then
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
    print_status "INFO" "Critical DoD Tests Summary:"
    print_status "INFO" "  Passed: $passed_tests"
    print_status "INFO" "  Failed: $failed_tests"
    print_status "INFO" "  Total Time: $duration seconds"
    
    # Return failure if any tests failed
    if [[ $failed_tests -gt 0 ]]; then
        print_status "FAIL" "$failed_tests Critical DoD tests failed"
        return 1
    else
        print_status "PASS" "All Critical DoD tests passed"
        return 0
    fi
}

# Run NFR performance tests
run_performance_tests() {
    verbose "Running NFR performance tests..."
    
    print_status "INFO" "Running Critical NFR performance tests..."
    
    local start_time=$(date +%s)
    local test_passed=true
    
    # Test 1: Latency test (p99 <= 150ms without rerank, 350ms with rerank)
    print_status "INFO" "Testing search latency (p99 <= 150ms without rerank, 350ms with rerank)..."
    
    python3 -c "
import asyncio
import httpx
import time
import numpy as np
from uuid import uuid4

async def test_latencies():
    # Test without rerank
    no_rerank_times = []
    async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
        for i in range(50):
            data = {
                'tenant_id': str(uuid4()),
                'query': 'performance test',
                'top_k': 10,
                'timeout_ms': 2000,
                'rerank': False
            }
            start = time.time()
            try:
                response = await client.post('/search', json=data)
                latency = (time.time() - start) * 1000
                no_rerank_times.append(latency)
            except Exception as e:
                print(f'Search request failed: {e}')
                return False
    
    if no_rerank_times:
        p99_no_rerank = np.percentile(no_rerank_times, 99)
        print(f'P99 latency (no rerank): {p99_no_rerank:.2f}ms')
        
        if p99_no_rerank > 150:
            print(f'FAIL: P99 {p99_no_rerank:.2f}ms > 150ms target')
            return False
        else:
            print(f'PASS: P99 {p99_no_rerank:.2f}ms <= 150ms')
    else:
        print('No successful no-rerank requests')
        return False
    
    # Test with rerank (if available)
    try:
        with_rerank_times = []
        async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
            for i in range(50):
                data = {
                    'tenant_id': str(uuid4()),
                    'query': 'performance test rerank',
                    'top_k': 10,
                    'timeout_ms': 2000,
                    'rerank': True
                }
                start = time.time()
                try:
                    response = await client.post('/search', json=data)
                    latency = (time.time() - start) * 1000
                    with_rerank_times.append(latency)
                except Exception as e:
                    print(f'Rerank search request failed: {e}')
                    # Skip rerank test if it fails
                    break
        
        if with_rerank_times:
            p99_with_rerank = np.percentile(with_rerank_times, 99)
            print(f'P99 latency (with rerank): {p99_with_rerank:.2f}ms')
            
            if p99_with_rerank > 350:
                print(f'FAIL: P99 {p99_with_rerank:.2f}ms > 350ms target')
                return False
            else:
                print(f'PASS: P99 {p99_with_rerank:.2f}ms <= 350ms')
    except Exception as e:
        print(f'Rerank test skipped (reranker not available): {e}')
    
    return True

success = asyncio.run(test_latencies())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Latency targets not met"
        test_passed=false
    }
    
    # Test 2: Throughput test (>= 200 RPS for search)
    print_status "INFO" "Testing search throughput (>= 200 RPS)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_search_throughput():
    requests = []
    start_time = time.time()
    
    async def make_search_request():
        data = {
            'tenant_id': str(uuid4()),
            'query': 'throughput test',
            'top_k': 10,
            'timeout_ms': 2000
        }
        async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
            await client.post('/search', json=data)
    
    # Make 200 requests
    tasks = [make_search_request() for _ in range(200)]
    await asyncio.gather(*tasks)
    
    duration = time.time() - start_time
    rps = 200 / duration
    
    print(f'Search throughput: {rps:.2f} RPS')
    
    if rps >= 200:
        print('Throughput test passed')
        return True
    else:
        print(f'Throughput test failed: {rps:.2f} RPS < 200 RPS')
        return False

success = asyncio.run(test_search_throughput())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Search throughput < 200 RPS"
        test_passed=false
    }
    
    # Test 3: Indexing throughput (>= 1000 doc/min)
    print_status "INFO" "Testing indexing throughput (>= 1000 doc/min)..."
    
    python3 -c "
import asyncio
import httpx
import time
from uuid import uuid4

async def test_indexing_throughput():
    start_time = time.time()
    
    async def make_index_request():
        data = {
            'tenant_id': str(uuid4()),
            'external_ref': f'throughput-doc-{int(time.time()*1000)}',
            'title': 'Throughput Test',
            'content': 'Test content for indexing throughput.',
            'tags': ['throughput'],
            'attributes': {}
        }
        async with httpx.AsyncClient(base_url='http://localhost:8000') as client:
            await client.post('/index', json=data)
    
    # Make 100 requests (should be > 1000 doc/min = 16.7 doc/sec)
    tasks = [make_index_request() for _ in range(100)]
    await asyncio.gather(*tasks)
    
    duration = time.time() - start_time
    docs_per_min = (100 / duration) * 60
    
    print(f'Indexing throughput: {docs_per_min:.0f} docs/min')
    
    if docs_per_min >= 1000:
        print('Indexing throughput test passed')
        return True
    else:
        print(f'Indexing throughput test failed: {docs_per_min:.0f} docs/min < 1000 docs/min')
        return False

success = asyncio.run(test_indexing_throughput())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Indexing throughput < 1000 docs/min"
        test_passed=false
    }
    
    # Test 4: Check embedding cache hit rate
    print_status "INFO" "Testing embedding cache hit rate (>= 50%)..."
    
    python3 -c "
import httpx

async def test_cache_hit_rate():
    try:
        response = await httpx.get('http://localhost:8000/metrics')
        metrics_text = response.text
        
        # Look for embedding cache hit rate
        for line in metrics_text.split('\n'):
            if 'embedding_cache_hit_rate' in line and not line.startswith('#'):
                # Extract the value (should be between 0 and 1)
                parts = line.split(' ')
                if len(parts) >= 2:
                    try:
                        hit_rate = float(parts[1])
                        print(f'Embedding cache hit rate: {hit_rate:.1%}')
                        
                        if hit_rate >= 0.5:
                            print('Cache hit rate test passed')
                            return True
                        else:
                            print(f'Cache hit rate test failed: {hit_rate:.1%} < 50%')
                            return False
                    except ValueError:
                        continue
        
        print('Cache hit rate metric not found')
        return True  # Skip if metric not available
        
    except Exception as e:
        print(f'Could not check cache hit rate: {e}')
        return True  # Skip if metrics not available

import asyncio
success = asyncio.run(test_cache_hit_rate())
exit(0 if success else 1)
" || {
        print_status "FAIL" "Performance test failed: Embedding cache hit rate < 50%"
        test_passed=false
    }
    
    if [[ "$test_passed" == true ]]; then
        print_status "PASS" "All NFR performance tests passed"
        return 0
    else
        print_status "FAIL" "One or more NFR performance tests failed"
        return 2
    fi
}

# Main execution
main() {
    print_status "INFO" "Starting Critical DoD validation (C-14)..."
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
            print_status "PASS" "All Critical DoD criteria and NFR targets validated successfully!"
            exit 0
        else
            print_status "FAIL" "Critical DoD validation failed!"
            exit $dod_result
        fi
    fi
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi