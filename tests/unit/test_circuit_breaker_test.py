"""C-03: Circuit breaker for reranker unit tests (ARCHITECT §6.6).

Tests rolling window circuit breaker: error rate threshold, latency p95 threshold,
state transitions (closed → open → half_open → closed), concurrent calls, metrics.
"""

import asyncio
import time
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY

from app.config import CircuitBreakerConfig
from app.observability.metrics import circuit_breaker_opened_total, circuit_breaker_requests_total, circuit_breaker_state
from app.reranker.circuit_breaker import CircuitBreakerOpen, RerankerCircuitBreaker


class TestRerankerCircuitBreaker:
    """Test RerankerCircuitBreaker core functionality."""

    def test_initial_state_closed(self) -> None:
        """Test circuit breaker starts in closed state."""
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        assert breaker.state() == "closed"
        assert not breaker.is_open()

    async def test_closed_state_calls_function(self) -> None:
        """Test that closed state calls the wrapped function."""
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        
        async def mock_fn():
            return "success"
        
        result = await breaker.call(mock_fn)
        assert result == "success"
        assert breaker.state() == "closed"

    async def test_open_state_returns_circuit_breaker_open(self) -> None:
        """Test that open state raises CircuitBreakerOpen."""
        config = CircuitBreakerConfig(error_rate_threshold=0.1)  # 10% threshold
        breaker = RerankerCircuitBreaker(config)
        
        # Force open state by recording many failures
        for _ in range(11):  # 11 failures out of 11 calls = 100% error rate
            await breaker._record_call(time.time() * 1000, 0, False)
        
        # Check and transition to open
        await breaker._check_and_transition()
        assert breaker.is_open()
        
        async def mock_fn():
            return "should_not_be_called"
        
        with pytest.raises(CircuitBreakerOpen):
            await breaker.call(mock_fn)

    async def test_error_rate_threshold_opens_breaker(self) -> None:
        """Test that error_rate_threshold opens circuit breaker."""
        config = CircuitBreakerConfig(error_rate_threshold=0.05)  # 5% threshold
        breaker = RerankerCircuitBreaker(config)
        
        # Record 5 failures out of 100 calls = 5% error rate
        for i in range(100):
            success = i < 95  # 95 successes, 5 failures
            await breaker._record_call(time.time() * 1000, 100 if success else 0, success)
        
        # Check and transition - should open
        await breaker._check_and_transition()
        assert breaker.is_open()

    async def test_latency_p95_threshold_opens_breaker(self) -> None:
        """Test that latency_p95_threshold opens circuit breaker."""
        config = CircuitBreakerConfig(latency_p95_threshold_ms=500)
        breaker = RerankerCircuitBreaker(config)
        
        # Record calls with high latency (some > 500ms)
        for i in range(100):
            latency = 600 if i < 6 else 100  # 6 calls with 600ms, rest with 100ms
            await breaker._record_call(time.time() * 1000, latency, True)
        
        # Check and transition - should open (p95 of [600,600,600,600,600,600,100,...] > 500)
        await breaker._check_and_transition()
        assert breaker.is_open()

    async def test_half_open_state_probes_one_call(self) -> None:
        """Test that half_open state allows one probe call."""
        config = CircuitBreakerConfig(cooldown_seconds=0.1)  # short cooldown for testing
        breaker = RerankerCircuitBreaker(config)
        
        # Force open state
        for _ in range(11):
            await breaker._record_call(time.time() * 1000, 0, False)
        await breaker._check_and_transition()
        assert breaker.is_open()
        
        # Wait for cooldown
        await asyncio.sleep(0.2)
        await breaker._check_and_transition()
        assert breaker.state() == "half_open"
        
        async def mock_fn():
            return "probe_success"
        
        # Half-open should allow one call
        result = await breaker.call(mock_fn)
        assert result == "probe_success"
        assert breaker.state() == "closed"  # successful probe should close

    async def test_half_open_failure_reopens_breaker(self) -> None:
        """Test that half_open failure reopens breaker and resets cooldown."""
        config = CircuitBreakerConfig(cooldown_seconds=0.1)
        breaker = RerankerCircuitBreaker(config)
        
        # Force open state
        for _ in range(11):
            await breaker._record_call(time.time() * 1000, 0, False)
        await breaker._check_and_transition()
        assert breaker.is_open()
        
        # Wait for cooldown
        await asyncio.sleep(0.2)
        await breaker._check_and_transition()
        assert breaker.state() == "half_open"
        
        async def mock_fn():
            raise ValueError("probe_failed")
        
        # Half-open failure should reopen breaker. call() не пробрасывает
        # исключение, а возвращает его как значение результата
        # (контракт зафиксирован в SearchOrchestrator._rerank).
        result = await breaker.call(mock_fn)
        
        assert isinstance(result, ValueError)
        assert breaker.is_open()
        assert breaker.state() == "open"
        assert time.time() - breaker._opened_at < 0.05  # cooldown just reset

    async def test_successful_half_open_closes_breaker(self) -> None:
        """Test that successful half-open call closes breaker."""
        config = CircuitBreakerConfig(cooldown_seconds=0.1)
        breaker = RerankerCircuitBreaker(config)
        
        # Force open state
        for _ in range(11):
            await breaker._record_call(time.time() * 1000, 0, False)
        await breaker._check_and_transition()
        assert breaker.is_open()
        
        # Wait for cooldown
        await asyncio.sleep(0.2)
        await breaker._check_and_transition()
        assert breaker.state() == "half_open"
        
        async def mock_fn():
            return "success"
        
        # Successful probe should close breaker
        result = await breaker.call(mock_fn)
        assert result == "success"
        assert breaker.state() == "closed"

    async def test_rolling_window_cleanup(self) -> None:
        """Test that old entries are cleaned from rolling window."""
        config = CircuitBreakerConfig(window_seconds=1.0)  # 1 second window
        breaker = RerankerCircuitBreaker(config)
        
        # Record old entries (older than 1 second)
        old_time = time.time() * 1000 - 2000  # 2 seconds ago
        for _ in range(10):
            await breaker._record_call(old_time, 100, True)
        
        # Record recent entries
        recent_time = time.time() * 1000
        for _ in range(5):
            await breaker._record_call(recent_time, 100, True)
        
        # Window should only contain recent entries
        assert len(breaker._window) == 5

    async def test_empty_window_does_not_open(self) -> None:
        """Test that empty window does not cause circuit breaker to open."""
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        
        # Empty window should not open
        await breaker._check_and_transition()
        assert breaker.state() == "closed"

    async def test_error_rate_calculation(self) -> None:
        """Test error rate calculation in rolling window."""
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        
        # Record 8 successes, 2 failures = 20% error rate
        for i in range(10):
            success = i < 8
            await breaker._record_call(time.time() * 1000, 100, success)
        
        assert breaker._error_rate() == 0.2

    async def test_latency_p95_calculation(self) -> None:
        """Test p95 latency calculation."""
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        
        # Record latencies: [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
        for latency in range(100, 1100, 100):
            await breaker._record_call(time.time() * 1000, latency, True)
        
        # p95 = 955: numpy.interp-linear по 0.95*(n-1) = 8.55,
        # т.е. между 900 и 1000 (не "среднее 9-го и 10-го" = 950).
        assert abs(breaker._latency_p95() - 955.0) < 1.0

    def test_state_value_mapping(self) -> None:
        """Test state to numeric value mapping for metrics."""
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        
        assert breaker._state_value() == 0  # closed = 0
        
        breaker._state = "half_open"
        assert breaker._state_value() == 1  # half_open = 1
        
        breaker._state = "open"
        assert breaker._state_value() == 2  # open = 2


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration with function calls."""

    async def test_concurrent_calls_error_rate(self) -> None:
        """Test concurrent calls reaching error rate threshold."""
        config = CircuitBreakerConfig(error_rate_threshold=0.05)  # 5% threshold
        breaker = RerankerCircuitBreaker(config)
        
        async def mock_fn(fail: bool = False):
            if fail:
                raise ValueError("simulated failure")
            return "success"
        
        # Record 200 concurrent calls with 11 failures (5.5% error rate)
        tasks = []
        for i in range(200):
            fail = i < 11  # first 11 calls fail
            task = asyncio.create_task(breaker.call(mock_fn, fail))
            tasks.append(task)
        
        # Wait for all calls to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check that breaker opened
        await breaker._check_and_transition()
        assert breaker.is_open()
        
        # Count exceptions (should be 11 failures + some CircuitBreakerOpen exceptions)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) >= 11  # at least 11 failures

    async def test_metrics_incrementation(self) -> None:
        """Test that circuit breaker metrics are actually incremented."""
        # Глобальный REGISTRY не трогаем: reload(app.observability.metrics)
        # падает с DuplicateTimeseries, потому что остальные модули уже держат
        # ссылки на зарегистрированные объекты. Считаем дельту счётчика.
        config = CircuitBreakerConfig()
        breaker = RerankerCircuitBreaker(config)
        
        async def mock_fn():
            return "success"
        
        successes = circuit_breaker_requests_total.labels(
            component="reranker", result="success"
        )
        before = successes._value.get()
        
        for _ in range(5):
            await breaker.call(mock_fn)
        
        assert successes._value.get() == before + 5


class TestCircuitBreakerConfig:
    """Test different circuit breaker configurations."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = CircuitBreakerConfig()
        assert config.error_rate_threshold == 0.05
        assert config.latency_p95_threshold_ms == 500
        assert config.window_seconds == 60
        assert config.cooldown_seconds == 60

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = CircuitBreakerConfig(
            error_rate_threshold=0.1,
            latency_p95_threshold_ms=1000,
            window_seconds=30,
            cooldown_seconds=120,
        )
        assert config.error_rate_threshold == 0.1
        assert config.latency_p95_threshold_ms == 1000
        assert config.window_seconds == 30
        assert config.cooldown_seconds == 120

    def test_config_bounds(self) -> None:
        """Test configuration validation bounds."""
        # Valid bounds
        config = CircuitBreakerConfig(error_rate_threshold=0.5)
        assert config.error_rate_threshold == 0.5
        
        # Test bounds are enforced by pydantic (this should not raise)
        config = CircuitBreakerConfig(error_rate_threshold=0.0)
        assert config.error_rate_threshold == 0.0
        
        config = CircuitBreakerConfig(error_rate_threshold=1.0)
        assert config.error_rate_threshold == 1.0