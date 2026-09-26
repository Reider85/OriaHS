#!/usr/bin/env python3
"""Simple smoke test to verify C-00 configuration changes work."""

import sys
import json
from pathlib import Path

def test_config_import():
    """Test that all new config sections can be imported."""
    try:
        from app.config import (
            AppConfig,
            RerankerConfig,
            CircuitBreakerConfig,
            EvalConfig,
            PushdownConfig,
            ThrottleConfig,
            FeatureFlags,
        )
        print("✓ All config classes imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Config import failed: {e}")
        return False

def test_schemas_import():
    """Test that updated schemas can be imported."""
    try:
        from app.api.schemas import SearchRequest, IndexResponse
        print("✓ Updated schemas imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Schema import failed: {e}")
        return False

def test_dataset_files():
    """Test that dataset files exist."""
    datasets_path = Path("eval/datasets/baseline_v1.jsonl")
    baselines_path = Path("eval/baselines/baseline_v1.json")
    
    if not datasets_path.exists():
        print("✗ Dataset file missing")
        return False
    
    if not baselines_path.exists():
        print("✗ Baseline file missing")
        return False
    
    # Check dataset has at least 100 lines
    with open(datasets_path, 'r', encoding='utf-8') as f:
        lines = sum(1 for _ in f)
    
    if lines < 100:
        print(f"✗ Dataset has only {lines} lines (expected >= 100)")
        return False
    
    print(f"✓ Dataset file exists with {lines} lines")
    print(f"✓ Baseline file exists")
    return True

def test_env_example():
    """Test that .env.example was updated."""
    env_path = Path(".env.example")
    
    if not env_path.exists():
        print("✗ .env.example missing")
        return False
    
    content = env_path.read_text(encoding='utf-8')
    
    required_vars = [
        "RERANKER_MODEL_NAME",
        "CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD",
        "EVAL_DATASET_PATH",
        "PUSHDOWN_SELECTIVITY_THRESHOLD",
        "THROTTLE_PENDING_WARN_THRESHOLD",
        "VECTOR_SEARCH_ENABLED",
    ]
    
    missing_vars = []
    for var in required_vars:
        if var not in content:
            missing_vars.append(var)
    
    if missing_vars:
        print(f"✗ Missing vars in .env.example: {missing_vars}")
        return False
    
    print("✓ .env.example contains all required variables")
    return True

def test_pyproject_toml():
    """Test that pyproject.toml was updated with new dependencies."""
    pyproject_path = Path("pyproject.toml")
    
    if not pyproject_path.exists():
        print("✗ pyproject.toml missing")
        return False
    
    content = pyproject_path.read_text(encoding='utf-8')
    
    required_deps = ["FlagEmbedding", "scikit-learn"]
    
    missing_deps = []
    for dep in required_deps:
        if dep not in content:
            missing_deps.append(dep)
    
    if missing_deps:
        print(f"✗ Missing dependencies in pyproject.toml: {missing_deps}")
        return False
    
    print("✓ pyproject.toml contains all required dependencies")
    return True

def main():
    """Run all smoke tests."""
    print("Running C-00 smoke tests...")
    print("=" * 50)
    
    tests = [
        test_config_import,
        test_schemas_import,
        test_dataset_files,
        test_env_example,
        test_pyproject_toml,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All C-00 changes verified successfully!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())