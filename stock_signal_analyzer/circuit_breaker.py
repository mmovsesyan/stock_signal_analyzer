"""Circuit breaker for external API calls.

Wraps functions with a state machine:
  CLOSED  → normal operation, count failures
  OPEN    → fast-fail after threshold reached, no requests sent
  HALF_OPEN → allow a probe after recovery timeout

Usage:
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
    @cb
    def fetch_data():
        return requests.get(url)

Or combine with retry:
    @circuit_breaker(failure_threshold=5, recovery_timeout=30)
    @retry_with_backoff(max_retries=2)
    def fetch_data():
        return requests.get(url)
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

_log = logging.getLogger(__name__)
T = TypeVar("T")


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is OPEN."""
    pass


class CircuitBreaker:
    """Thread-safe circuit breaker."""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        expected_exception: tuple[type[Exception], ...] = (Exception,),
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(1.0, recovery_timeout)
        self.expected_exception = expected_exception

        self._state = State.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state.value

    def _can_attempt(self) -> bool:
        if self._state == State.CLOSED:
            return True
        if self._state == State.OPEN:
            if self._last_failure_time is None:
                self._state = State.HALF_OPEN
                return True
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                _log.info("Circuit %s: timeout elapsed, probing HALF_OPEN", self.name)
                self._state = State.HALF_OPEN
                return True
            return False
        # HALF_OPEN
        return True

    def _on_success(self) -> None:
        with self._lock:
            if self._state == State.HALF_OPEN:
                _log.info("Circuit %s: probe succeeded, CLOSED", self.name)
                self._state = State.CLOSED
                self._failure_count = 0
                self._last_failure_time = None
            else:
                self._failure_count = max(0, self._failure_count - 1)

    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                if self._state != State.OPEN:
                    _log.warning(
                        "Circuit %s: OPEN after %d failures",
                        self.name,
                        self._failure_count,
                    )
                self._state = State.OPEN

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if not self._can_attempt():
                raise CircuitOpenError(
                    f"Circuit {self.name} is OPEN (last failure {self._last_failure_time:.0f})"
                )
            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
            except self.expected_exception:
                self._on_failure()
                raise

        return wrapper


# Pre-defined breakers for known external services
_polygon_breaker = CircuitBreaker(
    name="polygon",
    failure_threshold=5,
    recovery_timeout=60.0,
    expected_exception=(Exception,),
)

_finnhub_breaker = CircuitBreaker(
    name="finnhub",
    failure_threshold=5,
    recovery_timeout=60.0,
    expected_exception=(Exception,),
)

_yfinance_breaker = CircuitBreaker(
    name="yfinance",
    failure_threshold=5,
    recovery_timeout=60.0,
    expected_exception=(Exception,),
)

_tbank_breaker = CircuitBreaker(
    name="tbank",
    failure_threshold=5,
    recovery_timeout=60.0,
    expected_exception=(Exception,),
)


def circuit_breaker(
    service: str | None = None,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    expected_exception: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator factory.  If *service* is given, reuse the shared breaker."""
    if service == "polygon":
        breaker = _polygon_breaker
    elif service == "finnhub":
        breaker = _finnhub_breaker
    elif service == "yfinance":
        breaker = _yfinance_breaker
    elif service == "tbank":
        breaker = _tbank_breaker
    else:
        breaker = CircuitBreaker(
            name=service or "generic",
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
        )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        return breaker(func)

    return decorator
