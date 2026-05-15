"""
Subscription management — тарифы, лимиты, проверка доступа.

Тарифы:
  free    — 5 анализов/день, только US blue chips, без LLM
  pro     — 50 анализов/день, все рынки, LLM sentiment
  premium — безлимит, все рынки, LLM + per-user learning, приоритетная очередь

Переменные окружения:
  SUBSCRIPTION_ENABLED — включить проверку подписок (1/0, по умолчанию 0)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

_log = logging.getLogger(__name__)

SUBSCRIPTIONS_ENABLED = os.environ.get("SUBSCRIPTION_ENABLED", "0").strip() == "1"

# ── LRU-кэш дневных лимитов (обновляется из БД, переживает краткие restart-и) ──
# Формат: {user_id: (date_str, count)}
_usage_cache: dict[int, tuple[str, int]] = {}

# Кэш последнего известного тарифа (переживает кратковременные ошибки БД)
_tier_cache: dict[int, tuple[str, float]] = {}  # user_id: (tier, timestamp)
_TIER_CACHE_TTL = 300.0  # 5 минут


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_usage_db(user_id: int, today: str) -> int:
    """Получить счётчик из БД (fallback → 0)."""
    try:
        from .db import get_session, DailyUsage
        with get_session(read_only=True) as session:
            row = session.query(DailyUsage).filter_by(
                user_id=user_id, date=today
            ).first()
            return row.count if row else 0
    except Exception:
        return 0


def _increment_usage_db(user_id: int, today: str) -> None:
    """Атомарно инкрементировать счётчик (UPSERT)."""
    try:
        from .db import get_session, DailyUsage
        with get_session() as session:
            row = session.query(DailyUsage).filter_by(
                user_id=user_id, date=today
            ).first()
            if row:
                row.count += 1
            else:
                session.add(DailyUsage(user_id=user_id, date=today, count=1))
    except Exception:
        pass  # fail-open: если БД недоступна, лимит не блокирует


def _usage_for(user_id: int) -> int:
    """Получить текущий дневной счётчик (кэш + БД fallback)."""
    today = _today_key()
    cached = _usage_cache.get(user_id)
    if cached and cached[0] == today:
        return cached[1]
    # Cache miss или новый день — грузим из БД
    count = _get_usage_db(user_id, today)
    _usage_cache[user_id] = (today, count)
    return count


def _bump_usage(user_id: int) -> None:
    """Инкрементировать счётчик (кэш + БД)."""
    today = _today_key()
    _, count = _usage_cache.get(user_id, (today, 0))
    _usage_cache[user_id] = (today, count + 1)
    _increment_usage_db(user_id, today)


@dataclass
class TierLimits:
    """Лимиты тарифа."""
    name: str
    daily_analyses: int
    markets: list[str]  # ["US"], ["US", "RU"], ["US", "RU", "all"]
    llm_sentiment: bool
    per_user_learning: bool
    priority_queue: bool
    max_watchlist: int
    autocollect: bool
    notifications: bool


TIERS: dict[str, TierLimits] = {
    "free": TierLimits(
        name="Free",
        daily_analyses=5,
        markets=["US"],
        llm_sentiment=False,
        per_user_learning=False,
        priority_queue=False,
        max_watchlist=5,
        autocollect=False,
        notifications=False,
    ),
    "pro": TierLimits(
        name="Pro",
        daily_analyses=50,
        markets=["US", "RU"],
        llm_sentiment=True,
        per_user_learning=False,
        priority_queue=False,
        max_watchlist=30,
        autocollect=True,
        notifications=True,
    ),
    "premium": TierLimits(
        name="Premium",
        daily_analyses=999,
        markets=["US", "RU", "all"],
        llm_sentiment=True,
        per_user_learning=True,
        priority_queue=True,
        max_watchlist=100,
        autocollect=True,
        notifications=True,
    ),
}




def get_user_tier(user_id: int) -> str:
    """Получить тариф пользователя из БД или вернуть free.

    При временной недоступности БД использует кэш последнего известного тарифа
    чтобы не сбрасывать всех пользователей на free.
    """
    if not SUBSCRIPTIONS_ENABLED:
        return "premium"  # Если подписки выключены — всё доступно

    # Проверить кэш
    now = datetime.now(timezone.utc).timestamp()
    cached = _tier_cache.get(user_id)
    if cached is not None:
        cached_tier, cached_ts = cached
        if now - cached_ts < _TIER_CACHE_TTL:
            return cached_tier

    try:
        from .db import get_session, User
        with get_session(read_only=True) as session:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                _tier_cache[user_id] = ("free", now)
                return "free"
            # Проверить не истёк ли тариф
            if user.tier_expires_at and user.tier_expires_at < datetime.now(timezone.utc):
                user.tier = "free"
                user.tier_expires_at = None
                _tier_cache[user_id] = ("free", now)
                return "free"
            tier = user.tier or "free"
            _tier_cache[user_id] = (tier, now)
            return tier
    except Exception:
        # БД недоступна — используем кэш если есть, иначе free
        if cached is not None:
            _log.warning("DB unavailable for tier lookup, using cache for user %d", user_id)
            return cached[0]
        return "free"


def get_tier_limits(tier: str) -> TierLimits:
    """Получить лимиты тарифа."""
    return TIERS.get(tier, TIERS["free"])


def check_rate_limit(user_id: int) -> tuple[bool, str]:
    """
    Проверить, не превышен ли дневной лимит.
    Возвращает (allowed, message).
    Счётчик хранится в БД (persistent across restarts) + LRU-кэш in-memory.
    """
    if not SUBSCRIPTIONS_ENABLED:
        return True, ""

    tier = get_user_tier(user_id)
    limits = get_tier_limits(tier)
    used = _usage_for(user_id)

    if used >= limits.daily_analyses:
        return False, (
            f"Достигнут дневной лимит ({limits.daily_analyses} анализов для тарифа {limits.name}). "
            f"Обновите подписку для увеличения лимита."
        )

    _bump_usage(user_id)
    return True, ""


def check_market_access(user_id: int, symbol: str) -> tuple[bool, str]:
    """Проверить доступ к рынку."""
    if not SUBSCRIPTIONS_ENABLED:
        return True, ""

    tier = get_user_tier(user_id)
    limits = get_tier_limits(tier)

    if "all" in limits.markets:
        return True, ""

    is_ru = symbol.strip().upper().endswith(".ME")
    if is_ru and "RU" not in limits.markets:
        return False, f"Российский рынок доступен с тарифа Pro. Текущий: {limits.name}."

    return True, ""


def format_subscription_info(user_id: int) -> str:
    """Форматировать информацию о подписке для Telegram."""
    tier = get_user_tier(user_id)
    limits = get_tier_limits(tier)
    used = _usage_for(user_id)

    lines = [
        f"📋 Подписка: <b>{limits.name}</b>",
        f"Анализов сегодня: {used}/{limits.daily_analyses}",
        f"Рынки: {', '.join(limits.markets)}",
        f"LLM sentiment: {'✅' if limits.llm_sentiment else '❌'}",
        f"Автосбор: {'✅' if limits.autocollect else '❌'}",
        f"Уведомления: {'✅' if limits.notifications else '❌'}",
        f"Watchlist: до {limits.max_watchlist} тикеров",
    ]
    return "\n".join(lines)
