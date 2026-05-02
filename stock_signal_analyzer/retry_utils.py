"""Утилиты для обработки rate limits и retry логики."""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Декоратор для retry с exponential backoff.

    Args:
        max_retries: Максимальное количество попыток
        initial_delay: Начальная задержка в секундах
        backoff_factor: Множитель для увеличения задержки
        max_delay: Максимальная задержка между попытками
        retry_on: Кортеж исключений, при которых делать retry

    Example:
        @retry_with_backoff(max_retries=3, initial_delay=1.0)
        def fetch_data():
            return requests.get(url)
    """

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

                    # Проверяем, является ли это 429 (Too Many Requests)
                    is_rate_limit = False
                    if hasattr(e, "response") and hasattr(e.response, "status_code"):
                        is_rate_limit = e.response.status_code == 429

                    if attempt < max_retries:
                        # Для rate limit используем больший backoff
                        current_delay = delay if not is_rate_limit else delay * 2
                        current_delay = min(current_delay, max_delay)

                        _log.warning(
                            "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                            func.__name__,
                            attempt + 1,
                            max_retries + 1,
                            str(e)[:100],
                            current_delay,
                        )

                        time.sleep(current_delay)
                        delay *= backoff_factor
                    else:
                        _log.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_retries + 1,
                            str(e)[:200],
                        )

            # Если все попытки исчерпаны, пробрасываем последнее исключение
            if last_exception:
                raise last_exception
            raise RuntimeError(f"{func.__name__} failed without exception")

        return wrapper

    return decorator


class RateLimiter:
    """
    Простой rate limiter для ограничения частоты запросов.

    Example:
        limiter = RateLimiter(max_calls=5, period=60)  # 5 запросов в минуту

        @limiter.limit
        def api_call():
            return requests.get(url)
    """

    def __init__(self, max_calls: int, period: float):
        """
        Args:
            max_calls: Максимальное количество вызовов
            period: Период в секундах
        """
        self.max_calls = max_calls
        self.period = period
        self.calls: list[float] = []

    def limit(self, func: Callable[..., T]) -> Callable[..., T]:
        """Декоратор для ограничения частоты вызовов."""

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            now = time.time()

            # Удаляем старые вызовы за пределами периода
            self.calls = [call_time for call_time in self.calls if now - call_time < self.period]

            if len(self.calls) >= self.max_calls:
                # Вычисляем, сколько нужно подождать
                oldest_call = self.calls[0]
                wait_time = self.period - (now - oldest_call)

                if wait_time > 0:
                    _log.info(
                        "Rate limit reached for %s. Waiting %.1fs...",
                        func.__name__,
                        wait_time,
                    )
                    time.sleep(wait_time)
                    now = time.time()
                    self.calls = [call_time for call_time in self.calls if now - call_time < self.period]

            # Выполняем функцию
            result = func(*args, **kwargs)

            # Записываем время вызова
            self.calls.append(time.time())

            return result

        return wrapper

    def wait_if_needed(self) -> None:
        """Ждёт, если достигнут лимит вызовов."""
        now = time.time()
        self.calls = [call_time for call_time in self.calls if now - call_time < self.period]

        if len(self.calls) >= self.max_calls:
            oldest_call = self.calls[0]
            wait_time = self.period - (now - oldest_call)
            if wait_time > 0:
                _log.info("Rate limit reached. Waiting %.1fs...", wait_time)
                time.sleep(wait_time)
