"""Tests for cache layer (Redis + memory fallback)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from stock_signal_analyzer.cache import (
    _MemoryCache,
    _RedisCache,
    cache_analyze_key,
)


class TestMemoryCache:
    def test_get_miss(self):
        cache = _MemoryCache()
        assert cache.get("missing") is None

    def test_set_and_get(self):
        cache = _MemoryCache()
        cache.set("key1", {"score": 0.5}, ttl=60)
        assert cache.get("key1") == {"score": 0.5}

    def test_ttl_expires(self):
        cache = _MemoryCache()
        cache.set("key1", "value", ttl=0)
        assert cache.get("key1") is None

    def test_delete(self):
        cache = _MemoryCache()
        cache.set("key1", "value", ttl=60)
        cache.delete("key1")
        assert cache.get("key1") is None


class TestRedisCache:
    def test_redis_get_miss(self):
        mock_client = MagicMock()
        mock_client.get.return_value = None
        with patch("redis.from_url", return_value=mock_client):
            cache = _RedisCache("redis://localhost")
            assert cache.get("missing") is None

    def test_redis_set_and_get(self):
        mock_client = MagicMock()
        mock_client.get.return_value = '{"score": 0.5}'
        with patch("redis.from_url", return_value=mock_client):
            cache = _RedisCache("redis://localhost")
            result = cache.get("key1")
            assert result == {"score": 0.5}

    def test_redis_decode_error_returns_none(self):
        mock_client = MagicMock()
        mock_client.get.return_value = "not json"
        with patch("redis.from_url", return_value=mock_client):
            cache = _RedisCache("redis://localhost")
            assert cache.get("bad") is None


class TestKeyGeneration:
    def test_cache_analyze_key(self):
        assert cache_analyze_key("AAPL", fast_mode=True, use_finnhub_ws=False) == "analyze:AAPL:1:0"
        assert cache_analyze_key("aapl", fast_mode=False, use_finnhub_ws=True) == "analyze:AAPL:0:1"
