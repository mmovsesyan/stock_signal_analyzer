"""Tests for circuit breaker."""
from __future__ import annotations

import time

import pytest

from stock_signal_analyzer.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    circuit_breaker,
)


class TestCircuitBreaker:
    def test_closed_allows_calls(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == "closed"
        assert cb._can_attempt() is True

    def test_opens_after_failures(self):
        cb = CircuitBreaker(failure_threshold=2)

        @cb
        def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            fail()
        with pytest.raises(ValueError):
            fail()
        # Third call should raise CircuitOpenError
        with pytest.raises(CircuitOpenError):
            fail()
        assert cb.state == "open"

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.5)

        @cb
        def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            fail()
        assert cb.state == "open"
        time.sleep(0.6)
        # Should allow one probe
        assert cb._can_attempt() is True
        assert cb.state == "half_open"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.5)

        @cb
        def fail_once():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            fail_once()
        time.sleep(0.6)

        @cb
        def succeed():
            return "ok"

        result = succeed()
        assert result == "ok"
        assert cb.state == "closed"

    def test_shared_breaker_decorator(self):
        cb1 = circuit_breaker("polygon", failure_threshold=5)
        cb2 = circuit_breaker("polygon", failure_threshold=5)
        # Should reuse same underlying breaker instance
        f1 = cb1(lambda: "ok")
        f2 = cb2(lambda: "ok")
        # Both decorators wrap the same breaker state
        assert f1.__wrapped__ == f2.__wrapped__
