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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

SUBSCRIPTIONS_ENABLED = os.environ.get("SUBSCRIPTION_ENABLED", "0").strip() == "1"


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


# In-memory rate counter (per user daily usage)
_daily_usage: dict[int, dict[str, Any]] = {}


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_user_tier(user_id: int) -> str:
    """Получить тариф пользователя из БД или вернуть free."""
    if not SUBSCRIPTIONS_ENABLED:
        return "premium"  # Если подписки выключены — всё доступно

    try:
        from .db import get_session, User
        with get_session() as session:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return "free"
            # Проверить не истёк ли тариф
            if user.tier_expires_at and user.tier_expires_at < datetime.now(timezone.utc):
                user.tier = "free"
                user.tier_expires_at = None
                return "free"
            return user.tier or "free"
    except Exception:
        return "free"


def get_tier_limits(tier: str) -> TierLimits:
    """Получить лимиты тарифа."""
    return TIERS.get(tier, TIERS["free"])


def check_rate_limit(user_id: int) -> tuple[bool, str]:
    """
    Проверить, не превышен ли дневной лимит.
    Возвращает (allowed, message).
    """
    if not SUBSCRIPTIONS_ENABLED:
        return True, ""

    tier = get_user_tier(user_id)
    limits = get_tier_limits(tier)
    today = _today_key()

    if user_id not in _daily_usage:
        _daily_usage[user_id] = {"date": today, "count": 0}

    usage = _daily_usage[user_id]
    if usage["date"] != today:
        usage["date"] = today
        usage["count"] = 0

    if usage["count"] >= limits.daily_analyses:
        return False, (
            f"Достигнут дневной лимит ({limits.daily_analyses} анализов для тарифа {limits.name}). "
            f"Обновите подписку для увеличения лимита."
        )

    usage["count"] += 1
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
    today = _today_key()

    usage = _daily_usage.get(user_id, {})
    used = usage.get("count", 0) if usage.get("date") == today else 0

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
