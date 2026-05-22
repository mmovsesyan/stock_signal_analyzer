"""Утилиты для обработки rate limits и retry логики."""

from __future__ import annotations

import logging
import re
import threading
import time
from functools import wraps
from typing import Any, Callable, TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")

_SECRET_RE = re.compile(r"(token|key|apikey|api_key)=[^&\s]+", re.IGNORECASE)


def _sanitize_error(msg: str) -> str:
    return _SECRET_RE.sub(r"\1=***", msg)


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = initial_delay
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    is_rate_limit = False
                    if hasattr(e, "response") and hasattr(e.response, "status_code"):
                        is_rate_limit = e.response.status_code == 429
                    if attempt < max_retries:
                        current_delay = delay * 2 if is_rate_limit else delay
                        current_delay = min(current_delay, max_delay)
                        _log.warning(
                            "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                            func.__name__, attempt + 1, max_retries + 1,
                            _sanitize_error(str(e)[:200]), current_delay,
                        )
                        time.sleep(current_delay)
                        delay *= backoff_factor
                    else:
                        _log.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_retries + 1,
                            _sanitize_error(str(e)[:200]),
                        )
            if last_exception:
                raise last_exception
            raise RuntimeError(f"{func.__name__} failed without exception")
        return wrapper
    return decorator


class RateLimiter:
    """Token-bucket rate limiter с семафором. Thread-safe."""

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls: list[float] = []
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(max_calls)

    def _clean_old(self, now: float) -> None:
        self._calls = [t for t in self._calls if now - t < self.period]

    def _release_sem_after(self, delay: float) -> None:
        def _release():
            time.sleep(delay)
            try:
                self._sem.release()
            except ValueError:
                pass
        threading.Thread(target=_release, daemon=True).start()

    def limit(self, func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            self._sem.acquire()
            with self._lock:
                now = time.time()
                self._clean_old(now)
                if len(self._calls) >= self.max_calls:
                    oldest = self._calls[0]
                    wait_time = self.period - (now - oldest)
                    if wait_time > 0:
                        _log.info("Rate limit: waiting %.1fs for %s", wait_time, func.__name__)
                        time.sleep(wait_time)
                        now = time.time()
                        self._clean_old(now)
                call_time = time.time()
                self._calls.append(call_time)
            self._release_sem_after(self.period / max(self.max_calls, 1))
            try:
                return func(*args, **kwargs)
            except Exception as e:
                is_rate_limit = False
                if hasattr(e, "response") and hasattr(e.response, "status_code"):
                    is_rate_limit = e.response.status_code == 429
                if is_rate_limit:
                    with self._lock:
                        if call_time in self._calls:
                            self._calls.remove(call_time)
                raise
        return wrapper

    def wait_if_needed(self) -> None:
        with self._lock:
            now = time.time()
            self._clean_old(now)
            if len(self._calls) >= self.max_calls:
                oldest = self._calls[0]
                wait_time = self.period - (now - oldest)
                if wait_time > 0:
                    _log.info("Rate limit: waiting %.1fs...", wait_time)
                    time.sleep(wait_time)
