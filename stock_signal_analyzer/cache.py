"""Redis-backed cache layer for expensive computations.

Usage:
    from stock_signal_analyzer.cache import get_cache
    cache = get_cache()
    cache.set("analyze:AAPL", value, ttl=300)
    value = cache.get("analyze:AAPL")

Environment:
  REDIS_URL — Redis connection string (default: redis://localhost:6379/0)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis

_log = logging.getLogger(__name__)
_DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_DEFAULT_TTL = 300


class CacheBackend:
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        ...

    def delete(self, key: str) -> None:
        ...


class _RedisCache(CacheBackend):
    def __init__(self, redis_url: str) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        try:
            self._client.ping()
            _log.info("Redis cache connected")
        except redis.RedisError:
            raise

    def get(self, key: str) -> Any | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            _log.warning("Cache decode error for key %s", key)
            return None

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        try:
            self._client.setex(key, ttl, json.dumps(value, default=str))
        except redis.RedisError as exc:
            _log.warning("Cache set error: %s", exc)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except redis.RedisError:
            pass


class _MemoryCache(CacheBackend):
    """In-memory fallback (per-process, no TTL eviction beyond dict size)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._default_ttl = _DEFAULT_TTL

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry <= __import__("time").time():
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        import time
        self._store[key] = (value, time.time() + ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


def _build_cache() -> CacheBackend:
    redis_url = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
    if not redis_url or redis_url.lower() in ("none", "", "memory"):
        _log.info("REDIS_URL not set, using in-memory cache")
        return _MemoryCache()
    try:
        return _RedisCache(redis_url)
    except Exception as exc:
        _log.warning("Redis cache failed (%s), falling back to memory", exc)
        return _MemoryCache()


# Singleton
_cache_instance: CacheBackend | None = None


def get_cache() -> CacheBackend:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = _build_cache()
    return _cache_instance


def cache_analyze_key(symbol: str, fast_mode: bool = False, use_finnhub_ws: bool = False) -> str:
    return f"analyze:{symbol.upper()}:{int(fast_mode)}:{int(use_finnhub_ws)}"
