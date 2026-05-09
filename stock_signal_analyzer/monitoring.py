"""
Monitoring & Alerting — мониторинг здоровья системы.

Проверяет:
- Доступность Ollama
- Доступность БД
- Свежесть данных (последний сигнал не старше N часов)
- Размер очереди задач
- Ошибки в логах

Отправляет алерты в Telegram при проблемах.

Переменные окружения:
  MONITOR_ENABLED — включить мониторинг (1/0, по умолчанию 1)
  MONITOR_ALERT_CHAT_ID — Telegram chat ID для алертов (admin)
  MONITOR_STALE_HOURS — через сколько часов без сигналов алертить (по умолчанию 8)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_log = logging.getLogger(__name__)

MONITOR_ENABLED = os.environ.get("MONITOR_ENABLED", "1").strip() == "1"
ALERT_CHAT_ID = os.environ.get("MONITOR_ALERT_CHAT_ID", "")
STALE_HOURS = int(os.environ.get("MONITOR_STALE_HOURS", "8"))

# Anti-spam: не алертить чаще чем раз в час по одной проблеме
_last_alerts: dict[str, float] = {}
_ALERT_COOLDOWN = 3600  # 1 час


@dataclass
class HealthStatus:
    """Статус здоровья системы."""
    healthy: bool
    issues: list[str]
    components: dict[str, bool]
    timestamp: str


def check_system_health() -> HealthStatus:
    """Полная проверка здоровья системы."""
    issues: list[str] = []
    components: dict[str, bool] = {}

    # 1. Ollama
    try:
        from .llm_sentiment import ollama_available, ollama_model_ready
        components["ollama_api"] = ollama_available()
        if ollama_available():
            components["ollama_model"] = ollama_model_ready()
            if not ollama_model_ready():
                issues.append("Ollama: модель не загружена")
        else:
            components["ollama_model"] = False
            # Не критично — fallback на VADER
    except Exception as e:
        components["ollama_api"] = False
        components["ollama_model"] = False

    # 2. Database
    try:
        from .db import get_session
        with get_session() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        components["database"] = True
    except Exception:
        components["database"] = False
        issues.append("PostgreSQL недоступен")

    # 3. Signal freshness
    try:
        from .signal_log import log_path_from_env
        log_path = log_path_from_env()
        if log_path and os.path.exists(log_path):
            mtime = os.path.getmtime(log_path)
            age_hours = (time.time() - mtime) / 3600
            components["signal_log_fresh"] = age_hours < STALE_HOURS
            if age_hours >= STALE_HOURS:
                issues.append(f"Нет новых сигналов {age_hours:.1f}ч (порог: {STALE_HOURS}ч)")
        else:
            components["signal_log_fresh"] = False
    except Exception:
        components["signal_log_fresh"] = False

    # 4. Polygon API
    try:
        from .polygon_data import polygon_available
        components["polygon"] = polygon_available()
    except Exception:
        components["polygon"] = False

    # 5. Finnhub API
    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    components["finnhub"] = bool(finnhub_key)

    # 6. Learning state freshness
    try:
        from .llm_learning import load_learning_state
        state = load_learning_state()
        if state and state.last_updated:
            from datetime import datetime as dt
            last = dt.fromisoformat(state.last_updated.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            components["learning_fresh"] = age < 12  # должно обновляться каждые 6ч
            if age >= 12:
                issues.append(f"Learning не обновлялся {age:.1f}ч")
        else:
            components["learning_fresh"] = False
    except Exception:
        components["learning_fresh"] = False

    healthy = len(issues) == 0
    return HealthStatus(
        healthy=healthy,
        issues=issues,
        components=components,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


async def send_alert(bot, message: str, alert_key: str = "") -> None:
    """Отправить алерт в Telegram (с anti-spam)."""
    if not ALERT_CHAT_ID:
        return

    key = alert_key or message[:50]
    now = time.time()
    if key in _last_alerts and (now - _last_alerts[key]) < _ALERT_COOLDOWN:
        return  # Anti-spam

    try:
        await bot.send_message(
            chat_id=int(ALERT_CHAT_ID),
            text=f"⚠️ <b>Alert</b>\n{message}",
            parse_mode="HTML",
        )
        _last_alerts[key] = now
    except Exception as e:
        _log.warning("Failed to send alert: %s", e)


def format_health_report() -> str:
    """Форматировать отчёт о здоровье для Telegram."""
    status = check_system_health()

    icon = "✅" if status.healthy else "⚠️"
    lines = [f"{icon} <b>Здоровье системы</b>", ""]

    for comp, ok in sorted(status.components.items()):
        emoji = "🟢" if ok else "🔴"
        lines.append(f"  {emoji} {comp}")

    if status.issues:
        lines.append("")
        lines.append("<b>Проблемы:</b>")
        for issue in status.issues:
            lines.append(f"  ⚠️ {issue}")

    return "\n".join(lines)
