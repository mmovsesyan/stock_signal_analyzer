"""Unified user settings layer — БД first, fallback JSON.

Все настройки пользователя хранятся в PostgreSQL (таблица users).
При недоступности БД автоматически fallback на telegram_users.json
(через legacy user_store.py).

Использование:
    from stock_signal_analyzer.user_settings import load_user_settings, save_user_settings
    prefs = load_user_settings(user_id)
    prefs.signal_filter_type = "aggressive"
    save_user_settings(user_id, prefs)
"""

from __future__ import annotations

import logging
from typing import Any

from stock_signal_analyzer.user_store import (
    UserPrefs,
    load_prefs as _json_load_prefs,
    save_prefs as _json_save_prefs,
    normalize_symbol,
)
from stock_signal_analyzer.db import (
    db_available,
    get_session,
    User as DbUser,
    Watchlist as DbWatchlist,
    init_db,
)

_log = logging.getLogger(__name__)


def _db_user_to_prefs(user: DbUser) -> UserPrefs:
    """Конвертировать ORM User в UserPrefs dataclass."""
    detail = user.last_notify_detail or {}
    # Синхронизируем last_notify_ts из detail для совместимости
    last_notify_ts = {str(k): float(v["ts"]) for k, v in detail.items() if v.get("ts")}
    return UserPrefs(
        watchlist=user.watchlist_symbols(),
        notify_strong_outside=user.notify_strong_outside,
        strong_threshold=user.strong_threshold,
        notify_cooldown_sec=user.notify_cooldown_sec,
        last_notify_ts=last_notify_ts,
        last_notify_detail={str(k): dict(v) for k, v in detail.items()},
        autocollect_tickers=list(user.autocollect_tickers or []),
        use_default_tickers=user.use_default_tickers,
        tier=user.tier or "free",
        receive_learning_report=user.receive_learning_report,
        signal_filter_type=user.signal_filter_type or "balanced",
        language=user.language or "ru",
        auto_collect=user.auto_collect,
        max_watchlist_size=user.max_watchlist_size or 30,
        notify_drawdown=user.notify_drawdown,
        daily_digest=user.daily_digest,
        notify_outside_scope=user.notify_outside_scope or "all",
        last_bot_msg_id=user.last_bot_msg_id or 0,
        last_bot_msg_has_reply_kb=user.last_bot_msg_has_reply_kb or False,
    )


def _prefs_to_db_dict(prefs: UserPrefs) -> dict[str, Any]:
    """Конвертировать UserPrefs в dict для обновления ORM User."""
    return {
        "notify_strong_outside": prefs.notify_strong_outside,
        "strong_threshold": prefs.strong_threshold,
        "notify_cooldown_sec": prefs.notify_cooldown_sec,
        "use_default_tickers": prefs.use_default_tickers,
        "signal_filter_type": prefs.signal_filter_type,
        "language": prefs.language,
        "auto_collect": prefs.auto_collect,
        "receive_learning_report": prefs.receive_learning_report,
        "notify_drawdown": prefs.notify_drawdown,
        "daily_digest": prefs.daily_digest,
        "max_watchlist_size": prefs.max_watchlist_size,
        "autocollect_tickers": prefs.autocollect_tickers,
        "tier": prefs.tier,
        "notify_outside_scope": prefs.notify_outside_scope,
        "last_bot_msg_id": prefs.last_bot_msg_id,
        "last_bot_msg_has_reply_kb": prefs.last_bot_msg_has_reply_kb,
        "last_notify_detail": prefs.last_notify_detail,
    }


def ensure_user_exists(user_id: int, username: str | None = None) -> None:
    """Создать пользователя в БД если его там нет, или обновить username."""
    if not db_available():
        return
    try:
        with get_session() as session:
            user = session.query(DbUser).filter_by(telegram_id=user_id).first()
            if not user:
                user = DbUser(telegram_id=user_id, username=username or "", is_active=False)
                session.add(user)
                _log.info("Created DB user %d (pending approval)", user_id)
            elif username and username != user.username:
                old_username = user.username
                user.username = username
                _log.info("Updated username for user %d: %s -> %s", user_id, old_username, username)
    except Exception:
        _log.exception("ensure_user_exists failed for %d", user_id)


def load_user_settings(user_id: int) -> UserPrefs:
    """Загрузить настройки пользователя.

    Priority:
        1. PostgreSQL (users table)
        2. Fallback JSON (telegram_users.json)
        3. Defaults (UserPrefs())
    """
    # 1. Попробовать БД
    if db_available():
        try:
            with get_session(read_only=True) as session:
                user = session.query(DbUser).filter_by(telegram_id=user_id).first()
                if user:
                    return _db_user_to_prefs(user)
        except Exception:
            _log.warning("DB read failed for user %d, falling back to JSON", user_id, exc_info=True)

    # 2. Fallback JSON
    try:
        return _json_load_prefs(user_id)
    except Exception:
        _log.warning("JSON read failed for user %d, using defaults", user_id, exc_info=True)

    # 3. Defaults
    return UserPrefs()


def _sync_watchlist_db(session, user: DbUser, watchlist: list[str]) -> None:
    """Синхронизировать watchlist пользователя в БД."""
    existing = {w.symbol for w in user.watchlist}
    wanted = {normalize_symbol(s) for s in watchlist}
    # Удалить лишние
    for w in list(user.watchlist):
        if w.symbol not in wanted:
            session.delete(w)
    # Добавить новые
    for sym in wanted:
        if sym and sym not in existing:
            user.watchlist.append(DbWatchlist(symbol=sym))


def save_user_settings(user_id: int, prefs: UserPrefs, username: str | None = None) -> None:
    """Сохранить настройки пользователя.

    Priority:
        1. PostgreSQL (users table)
        2. Fallback JSON (telegram_users.json)
    """
    # 1. Попробовать БД
    if db_available():
        try:
            with get_session() as session:
                user = session.query(DbUser).filter_by(telegram_id=user_id).first()
                if not user:
                    user = DbUser(telegram_id=user_id, username=username or "")
                    session.add(user)
                for key, value in _prefs_to_db_dict(prefs).items():
                    setattr(user, key, value)
                _sync_watchlist_db(session, user, prefs.watchlist)
                _log.info("Saved settings for user %d to DB", user_id)
                return
        except Exception:
            _log.warning("DB write failed for user %d, falling back to JSON", user_id, exc_info=True)

    # 2. Fallback JSON
    try:
        _json_save_prefs(user_id, prefs)
        _log.info("Saved settings for user %d to JSON", user_id)
    except Exception:
        _log.exception("JSON save failed for user %d", user_id)


# Legacy aliases for backward compatibility in existing code
load_prefs = load_user_settings
save_prefs = save_user_settings


def all_user_ids() -> list[int]:
    """Вернуть список всех telegram_id пользователей.

    Priority:
        1. PostgreSQL (users table)
        2. Fallback JSON (telegram_users.json)
    """
    # 1. Попробовать БД
    if db_available():
        try:
            with get_session(read_only=True) as session:
                rows = session.query(DbUser.telegram_id).filter_by(is_active=True).all()
                return [r[0] for r in rows if r[0] is not None]
        except Exception:
            _log.warning("DB read for all_user_ids failed, falling back to JSON", exc_info=True)

    # 2. Fallback JSON
    try:
        from stock_signal_analyzer.user_store import all_user_ids as _json_all_user_ids
        return _json_all_user_ids()
    except Exception:
        _log.warning("JSON read for all_user_ids failed", exc_info=True)

    return []
