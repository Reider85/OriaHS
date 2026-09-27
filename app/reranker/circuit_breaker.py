"""Circuit breaker for reranker service (C-03, ARCHITECT §6.6).

Implements rolling window circuit breaker for RerankerService to prevent
cascading timeouts when cross-encoder degrades. Opens when error_rate > 5%
or latency_p95 > 500ms, stays open for 60 seconds, then probes with half-open state.
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable, Literal

import numpy as np
from prometheus_client import Counter, Gauge

from app.config import CircuitBreakerConfig
from app.observability.metrics import circuit_breaker_opened_total, circuit_breaker_requests_total, circuit_breaker_state

logger = logging.getLogger(__name__)


class CircuitBreakerOpen(Exception):
    """Sentinel value returned when circuit breaker is open."""
    pass


class RerankerCircuitBreaker:
    """Circuit breaker for RerankerService with rolling window and half-open state.
    
    States: closed (normal) → open (rerank disabled) → half_open (probe) → closed.
    Opens when error_rate > error_rate_threshold or latency_p95 > latency_p95_threshold_ms.
    """
    
    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        self._state: Literal["closed", "open", "half_open"] = "closed"
        self._opened_at: float = 0.0  # timestamp when opened
        self._window: deque[tuple[float, int, bool]] = deque()  # (timestamp_ms, latency_ms, success)
        self._lock = asyncio.Lock()
    
    async def call(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """Wrap function call with circuit breaker logic.
        
        Parameters
        ----------
        fn
            Function to wrap (typically RerankerService.rerank).
        *args, **kwargs
            Arguments to pass to the function.
            
        Returns
        -------
        Any
            Result of fn() call, or None if circuit breaker is open.
            
        Raises
        ------
        CircuitBreakerOpen
            When circuit breaker is open (early return without calling fn).
        """
        # Update metrics for current state
        circuit_breaker_state.set(self._state_value())
        
        if self._state == "open":
            # Log warning and return sentinel
            logger.warning(
                "Circuit breaker open",
                extra={
                    "reason": "circuit breaker open",
                    "until": time.time() + self._config.cooldown_seconds,
                    "state": self._state,
                },
            )
            circuit_breaker_requests_total.labels(result="rejected").inc()
            raise CircuitBreakerOpen("Circuit breaker is open")
        
        try:
            # Call the wrapped function
            start_time = time.time()
            result = await fn(*args, **kwargs)
            latency_ms = int((time.time() - start_time) * 1000)
            success = True
            
            circuit_breaker_requests_total.labels(result="success").inc()
            
        except Exception as exc:
            latency_ms = 0  # unknown latency for failed calls
            success = False
            result = exc
            circuit_breaker_requests_total.labels(result="error").inc()
        
        # Record the call result in rolling window
        await self._record_call(time.time() * 1000, latency_ms, success)
        
        # Check if we should transition to open state
        await self._check_and_transition()
        
        # If we're in half-open state and this was a success, transition back to closed
        if self._state == "half_open" and success:
            async with self._lock:
                if self._state == "half_open":
                    self._state = "closed"
                    logger.info("Circuit breaker closed after successful probe")
        
        # If we're in half-open state and this was a failure, stay open
        if self._state == "half_open" and not success:
            async with self._lock:
                if self._state == "half_open":
                    self._state = "open"
                    self._opened_at = time.time()
                    logger.warning(
                        "Circuit breaker reopened after failed probe",
                        extra={"cooldown_seconds": self._config.cooldown_seconds},
                    )
                    circuit_breaker_opened_total.inc()
        
        return result
    
    def is_open(self) -> bool:
        """Check if circuit breaker is currently open (rerank disabled)."""
        return self._state == "open"
    
    def state(self) -> Literal["closed", "open", "half_open"]:
        """Get current circuit breaker state."""
        return self._state
    
    async def _record_call(self, timestamp_ms: float, latency_ms: int, success: bool) -> None:
        """Record a call result in the rolling window.
        
        Parameters
        ----------
        timestamp_ms
            Unix timestamp in milliseconds.
        latency_ms
            Call latency in milliseconds (0 for failed calls).
        success
            Whether the call succeeded.
        """
        # Clean old entries from rolling window
        cutoff_time = timestamp_ms - (self._config.window_seconds * 1000)
        while self._window and self._window[0][0] < cutoff_time:
            self._window.popleft()
        
        # Add new entry
        self._window.append((timestamp_ms, latency_ms, success))
    
    async def _check_and_transition(self) -> None:
        """Check if circuit breaker should open and transition states."""
        async with self._lock:
            if self._state == "closed":
                # Check if we should open
                if self._should_open():
                    self._state = "open"
                    self._opened_at = time.time()
                    logger.warning(
                        "Circuit breaker opened",
                        extra={
                            "error_rate": self._error_rate(),
                            "latency_p95_ms": self._latency_p95(),
                            "error_rate_threshold": self._config.error_rate_threshold,
                            "latency_p95_threshold_ms": self._config.latency_p95_threshold_ms,
                            "cooldown_seconds": self._config.cooldown_seconds,
                        },
                    )
                    circuit_breaker_opened_total.inc()
            
            elif self._state == "open":
                # Check if we should transition to half-open
                if time.time() - self._opened_at >= self._config.cooldown_seconds:
                    self._state = "half_open"
                    logger.info("Circuit breaker half-open (ready for probe)")
    
    def _should_open(self) -> bool:
        """Check if circuit breaker should open based on error rate and latency."""
        if not self._window:
            return False
        
        # Check error rate threshold
        error_rate = self._error_rate()
        if error_rate > self._config.error_rate_threshold:
            return True
        
        # Check latency p95 threshold
        latency_p95 = self._latency_p95()
        if latency_p95 > self._config.latency_p95_threshold_ms:
            return True
        
        return False
    
    def _error_rate(self) -> float:
        """Calculate current error rate in the rolling window."""
        if not self._window:
            return 0.0
        
        total_calls = len(self._window)
        failed_calls = sum(1 for _, _, success in self._window if not success)
        return failed_calls / total_calls if total_calls > 0 else 0.0
    
    def _latency_p95(self) -> float:
        """Calculate p95 latency in the rolling window."""
        if not self._window:
            return 0.0
        
        # Get latencies from successful calls only
        latencies = [latency for _, latency, success in self._window if success]
        if not latencies:
            return 0.0
        
        return float(np.percentile(latencies, 95))
    
    def _state_value(self) -> int:
        """Get numeric value for circuit breaker state (for Prometheus gauge)."""
        return {"closed": 0, "half_open": 1, "open": 2}[self._state]