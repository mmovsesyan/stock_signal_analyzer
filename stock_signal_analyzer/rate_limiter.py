"""Redis-backed sliding-window rate limiter with in-memory fallback.

Uses Redis sorted sets for per-client sliding-window counting.
Falls back to in-memory dict if Redis is unavailable.

Environment:
  REDIS_URL — Redis connection string (default: redis://localhost:6379/0)
  API_RATE_LIMIT_PER_MIN — default rate limit (default: 30)
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Protocol

import redis

_log = logging.getLogger(__name__)

_DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_DEFAULT_LIMIT = int(os.environ.get("API_RATE_LIMIT_PER_MIN", "30"))
_WINDOW_SEC = 60.0


class RateLimiter(Protocol):
    def is_allowed(self, client_id: str, limit: int) -> bool:
        ...


class _MemoryLimiter:
    """In-memory sliding window (fallback when Redis is down)."""

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str, limit: int) -> bool:
        now = time.time()
        calls = self._store.get(client_id, [])
        recent = [t for t in calls if now - t < _WINDOW_SEC]
        if not recent:
            self._store[client_id] = [now]
            return True
        if len(recent) >= limit:
            return False
        recent.append(now)
        self._store[client_id] = recent
        return True


class _RedisLimiter:
    """Redis-backed sliding window using sorted sets."""

    def __init__(self, redis_url: str) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        try:
            self._client.ping()
            _log.info("Redis rate limiter connected")
        except redis.RedisError as exc:
            _log.warning("Redis ping failed: %s", exc)
            raise

    def is_allowed(self, client_id: str, limit: int) -> bool:
        key = f"rate_limit:{client_id}"
        now = time.time()
        pipe = self._client.pipeline()
        # Remove entries older than window
        pipe.zremrangebyscore(key, 0, now - _WINDOW_SEC)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Count remaining
        pipe.zcard(key)
        # Ensure key expires after window
        pipe.expire(key, int(_WINDOW_SEC) + 1)
        _, _, count, _ = pipe.execute()
        return int(count) <= limit


def _build_limiter() -> RateLimiter:
    redis_url = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
    if not redis_url or redis_url.lower() in ("none", "", "memory"):
        _log.info("REDIS_URL not set, using in-memory rate limiter")
        return _MemoryLimiter()
    try:
        return _RedisLimiter(redis_url)
    except Exception as exc:
        _log.warning("Redis rate limiter failed (%s), falling back to memory", exc)
        return _MemoryLimiter()


# Singleton instance
_limiter: RateLimiter = _build_limiter()


def is_allowed(client_id: str, limit: int = _DEFAULT_LIMIT) -> bool:
    """Check if a request from *client_id* is allowed under *limit* per minute."""
    return _limiter.is_allowed(client_id, limit)


def reset_limiter() -> None:
    """Reset the internal singleton (useful for testing)."""
    global _limiter
    _limiter = _build_limiter()
