"""Валидация конфигурации при старте.

Проверяет обязательные переменные окружения и выводит понятные сообщения
о том, что нужно задать, вместо cryptic crash в рантайме.
"""
from __future__ import annotations

import os
import logging

_log = logging.getLogger(__name__)

_REQUIRED_VARS = [
    "TELEGRAM_BOT_TOKEN",
]

_OPTIONAL_VARS = [
    "FINNHUB_API_KEY",
    "POLYGON_API_KEY",
    "TINKOFF_INVEST_TOKEN",
    "ADMIN_CHAT_ID",
    "ADMIN_USER_ID",
    "API_SECRET_KEY",
    "ALLOWED_ORIGINS",
    "ALLOW_ALL_USERS",
]


def validate_telegram_config() -> None:
    """Проверить конфигурацию для Telegram бота."""
    missing = [v for v in _REQUIRED_VARS if not os.environ.get(v, "").strip()]
    if missing:
        vars_list = ", ".join(missing)
        raise ValueError(
            f"Не заданы обязательные переменные окружения: {vars_list}. "
            f"Добавьте их в .env файл или экспортируйте перед запуском."
        )

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token and not token.startswith(("5", "6", "7", "8")):
        _log.warning(
            "TELEGRAM_BOT_TOKEN выглядит подозрительно (не начинается с 5-8). "
            "Убедитесь, что токен корректен."
        )


def validate_api_config() -> None:
    """Проверить конфигурацию для API."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        _log.warning(
            "TELEGRAM_BOT_TOKEN не задан. API запущен, но бот не будет работать."
        )

    secret = os.environ.get("API_SECRET_KEY", "").strip()
    allowed = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if not secret:
        raise ValueError(
            "API_SECRET_KEY не задан — API эндпоинты открыты без аутентификации. "
            "Задайте API_SECRET_KEY в .env и перезапустите."
        )
    if not allowed:
        raise ValueError(
            "ALLOWED_ORIGINS не задан — CORS разрешает все источники. "
            "Задайте ALLOWED_ORIGINS в .env (например, ALLOWED_ORIGINS=https://yourdomain.com) и перезапустите."
        )

    allow_all = os.environ.get("ALLOW_ALL_USERS", "").strip().lower()
    if allow_all in ("1", "true", "yes"):
        _log.warning(
            "⚠️ ALLOW_ALL_USERS включен — все пользователи имеют безлимитный доступ. "
            "НИКОГДА не включайте это в продакшене."
        )
