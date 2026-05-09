"""
Уведомления в мессенджер MAX (российский мессенджер).

Отправляет сигналы и алерты в MAX параллельно с Telegram.
Использует библиотеку maxapi (pip install maxapi).

Переменные окружения:
  MAX_BOT_TOKEN     — токен бота MAX (получить у @MasterBot в MAX)
  MAX_CHAT_ID       — ID чата/канала для уведомлений
  MAX_NOTIFY        — включить уведомления в MAX (1/0, по умолчанию 1 если токен задан)

Установка:
  pip install maxapi

Как получить токен:
  1) Откройте MAX, найдите @MasterBot
  2) Отправьте /newbot
  3) Следуйте инструкциям, скопируйте токен
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

_MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
_MAX_CHAT_ID = os.environ.get("MAX_CHAT_ID", "").strip()
_MAX_ENABLED = os.environ.get("MAX_NOTIFY", "1").strip() != "0"


def max_available() -> bool:
    """Проверить, настроен ли MAX бот."""
    if not _MAX_ENABLED or not _MAX_BOT_TOKEN or not _MAX_CHAT_ID:
        return False
    try:
        import maxapi  # noqa: F401
        return True
    except ImportError:
        return False


async def send_max_message(text: str, chat_id: str | None = None) -> bool:
    """
    Отправить сообщение в MAX.
    Возвращает True если успешно.
    """
    if not max_available():
        return False

    target_chat = chat_id or _MAX_CHAT_ID
    if not target_chat:
        return False

    try:
        from maxapi import Bot

        bot = Bot(token=_MAX_BOT_TOKEN)
        await bot.send_message(chat_id=int(target_chat), text=text)
        return True
    except Exception as e:
        _log.warning("MAX send failed: %s", e)
        return False


async def send_signal_to_max(symbol: str, tier: str, score: float, direction: str, summary: str) -> bool:
    """
    Отправить краткий сигнал в MAX.
    Формат адаптирован под MAX (без HTML, простой текст).
    """
    icon = "🟢" if direction == "long" else ("🔴" if direction == "short" else "⚪")
    dir_label = "LONG" if direction == "long" else ("SHORT" if direction == "short" else "NEUTRAL")

    text = (
        f"{icon} {symbol} | {dir_label} | Класс {tier}\n"
        f"Score: {score:+.3f}\n"
        f"{summary}\n"
        f"─────────────────\n"
        f"Stock Signal Analyzer"
    )
    return await send_max_message(text)


async def send_new_user_alert_max(
    full_name: str,
    username: str,
    user_id: int,
    plan: str,
) -> bool:
    """Уведомить админа в MAX о новом пользователе."""
    text = (
        f"🆕 Новый пользователь\n"
        f"Имя: {full_name}\n"
        f"Username: @{username}\n"
        f"ID: {user_id}\n"
        f"План: {plan}\n"
        f"─────────────────\n"
        f"Одобрите в Telegram: /approve {user_id}"
    )
    return await send_max_message(text)


def send_max_sync(text: str) -> bool:
    """Синхронная обёртка для отправки (для использования из non-async кода)."""
    if not max_available():
        return False
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Если уже в async контексте — создаём task
            asyncio.ensure_future(send_max_message(text))
            return True
        else:
            return loop.run_until_complete(send_max_message(text))
    except RuntimeError:
        # Нет event loop — создаём новый
        return asyncio.run(send_max_message(text))
