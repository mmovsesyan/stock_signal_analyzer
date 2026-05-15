#!/usr/bin/env python3
"""Миграция настроек пользователей из telegram_users.json в PostgreSQL.

Использование:
    python scripts/migrate_user_settings.py

Переносит:
    - Профиль (tier, username, created_at)
    - Preferences (signal_filter_type, language, auto_collect, notify_drawdown, ...)
    - Watchlist (список тикеров)
    - Autocollect tickers

После успешной миграции создаёт backup: telegram_users.json.migrated.<timestamp>
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Добавить корень проекта в sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from stock_signal_analyzer.db import db_available, get_session, init_db, User, Watchlist
from stock_signal_analyzer.user_store import _default_data_path, _load_raw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)


def migrate() -> None:
    if not db_available():
        _log.error("БД недоступна. Проверьте DATABASE_URL.")
        sys.exit(1)

    # Убедиться что таблицы существуют
    init_db()

    data_path = _default_data_path()
    if not data_path.exists():
        _log.info("telegram_users.json не найден — нечего мигрировать.")
        sys.exit(0)

    raw = _load_raw(data_path)
    users_data = raw.get("users", {})
    if not users_data:
        _log.info("Нет пользователей для миграции.")
        sys.exit(0)

    migrated = 0
    skipped = 0

    with get_session() as session:
        for user_id_str, user_data in users_data.items():
            try:
                telegram_id = int(user_id_str)
            except ValueError:
                _log.warning("Пропуск некорректного user_id: %s", user_id_str)
                skipped += 1
                continue

            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                user = User(telegram_id=telegram_id)
                session.add(user)

            # Обновить поля
            user.username = user_data.get("username") or user_data.get("user_name") or user.username
            user.tier = user_data.get("tier", user.tier or "free")
            user.notify_strong_outside = user_data.get("notify_strong_outside", user.notify_strong_outside)
            user.strong_threshold = float(user_data.get("strong_threshold", user.strong_threshold or 0.35))
            user.notify_cooldown_sec = int(user_data.get("notify_cooldown_sec", user.notify_cooldown_sec or 86400))
            user.use_default_tickers = user_data.get("use_default_tickers", user.use_default_tickers)
            user.signal_filter_type = user_data.get("signal_filter_type", user.signal_filter_type or "balanced")
            user.language = user_data.get("language", user.language or "ru")
            user.auto_collect = user_data.get("auto_collect", user.auto_collect)
            user.receive_learning_report = user_data.get("receive_learning_report", user.receive_learning_report)
            user.notify_drawdown = user_data.get("notify_drawdown", user.notify_drawdown)
            user.daily_digest = user_data.get("daily_digest", user.daily_digest)
            user.max_watchlist_size = int(user_data.get("max_watchlist_size", user.max_watchlist_size or 30))
            user.autocollect_tickers = list(user_data.get("autocollect_tickers") or [])

            # Миграция watchlist
            watchlist_symbols = user_data.get("watchlist", [])
            if watchlist_symbols:
                existing = {w.symbol for w in user.watchlist}
                for sym in watchlist_symbols:
                    sym = str(sym).strip().upper()
                    if sym and sym not in existing:
                        user.watchlist.append(Watchlist(symbol=sym))

            migrated += 1
            _log.info("Мигрирован пользователь %d", telegram_id)

    _log.info("Миграция завершена: %d пользователей мигрировано, %d пропущено.", migrated, skipped)

    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = data_path.with_suffix(f".json.migrated.{ts}")
    data_path.rename(backup_path)
    _log.info("Backup создан: %s", backup_path)


if __name__ == "__main__":
    migrate()
