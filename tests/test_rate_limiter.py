"""Tests for Redis-backed and memory rate limiters."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from stock_signal_analyzer.rate_limiter import (
    _build_limiter,
    _MemoryLimiter,
    _RedisLimiter,
    is_allowed,
    reset_limiter,
)


class TestMemoryLimiter:
    def test_first_request_allowed(self):
        lim = _MemoryLimiter()
        assert lim.is_allowed("client1", limit=3) is True

    def test_requests_within_limit(self):
        lim = _MemoryLimiter()
        assert lim.is_allowed("client1", limit=3) is True
        assert lim.is_allowed("client1", limit=3) is True
        assert lim.is_allowed("client1", limit=3) is True

    def test_request_over_limit_blocked(self):
        lim = _MemoryLimiter()
        lim.is_allowed("client1", limit=2)
        lim.is_allowed("client1", limit=2)
        assert lim.is_allowed("client1", limit=2) is False

    def test_window_resets_after_60s(self):
        lim = _MemoryLimiter()
        lim.is_allowed("client1", limit=1)
        assert lim.is_allowed("client1", limit=1) is False
        # Simulate 61s passing by clearing old calls manually
        lim._store["client1"] = [time.time() - 61]
        assert lim.is_allowed("client1", limit=1) is True


class TestRedisLimiter:
    def test_redis_limiter_allows_within_limit(self):
        mock_client = MagicMock()
        mock_client.pipeline.return_value = mock_client
        mock_client.execute.return_value = [0, 1, 1, True]
        with patch("redis.from_url", return_value=mock_client):
            lim = _RedisLimiter("redis://localhost")
            assert lim.is_allowed("client1", limit=3) is True

    def test_redis_limiter_blocks_over_limit(self):
        mock_client = MagicMock()
        mock_client.pipeline.return_value = mock_client
        mock_client.execute.return_value = [0, 1, 4, True]
        with patch("redis.from_url", return_value=mock_client):
            lim = _RedisLimiter("redis://localhost")
            assert lim.is_allowed("client1", limit=3) is False

    def test_redis_connection_failure_falls_back(self):
        with patch("redis.from_url", side_effect=Exception("Redis down")):
            lim = _build_limiter()
            assert isinstance(lim, _MemoryLimiter)
            assert lim.is_allowed("client1", limit=3) is True


class TestIntegration:
    def test_is_allowed_singleton(self):
        reset_limiter()
        assert is_allowed("client1", limit=3) is True
        assert is_allowed("client1", limit=3) is True
        assert is_allowed("client1", limit=3) is True
        assert is_allowed("client1", limit=3) is False
