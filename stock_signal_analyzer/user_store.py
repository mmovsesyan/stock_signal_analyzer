"""Настройки пользователя Telegram: список бумаг, уведомления о сильных сигналах вне списка."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def _default_data_path() -> Path:
    env = os.environ.get("STOCK_SIGNAL_DATA")
    if env:
        return Path(env) / "telegram_users.json"
    root = Path(__file__).resolve().parent.parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "telegram_users.json"


_lock = threading.Lock()

# In-memory кэш: исключает повторное чтение файла при каждом вызове load_prefs/save_prefs.
_cache: dict[str, Any] | None = None
_cache_path: Path | None = None


@dataclass
class UserPrefs:
    watchlist: list[str] = field(default_factory=list)
    """Уведомлять о сильном сигнале по бумагам не из списка."""
    notify_strong_outside: bool = True
    """Порог |score| для «сильного» сигнала."""
    strong_threshold: float = 0.35
    """Не спамить одной и той же бумагей чаще (секунды)."""
    notify_cooldown_sec: int = 86_400
    """Последнее уведомление: тикер -> unix time."""
    last_notify_ts: dict[str, float] = field(default_factory=dict)
    """Тикеры для автосбора (если пусто - используются дефолтные)."""
    autocollect_tickers: list[str] = field(default_factory=list)
    """Использовать дефолтные тикеры в автосборе."""
    use_default_tickers: bool = True
    """Выбранный план подписки: free, pro, premium."""
    tier: str = "free"
    """Получать learning report (1 = да, 0 = нет)."""
    receive_learning_report: bool = False
    """Тип фильтра сигналов: conservative, balanced, aggressive."""
    signal_filter_type: str = "balanced"
    """Язык интерфейса: ru, en."""
    language: str = "ru"
    """Включить автосбор сигналов."""
    auto_collect: bool = False
    """Максимальный размер watchlist."""
    max_watchlist_size: int = 30
    """Получать уведомления о просадках (circuit breaker)."""
    notify_drawdown: bool = True
    """Получать ежедневный дайджест."""
    daily_digest: bool = False


def _empty_store() -> dict[str, Any]:
    return {"users": {}}


def _load_raw(path: Path) -> dict[str, Any]:
    global _cache, _cache_path
    # Возвращаем кэш если path тот же
    if _cache is not None and _cache_path == path:
        return _cache
    # Первый вызов или смена пути — читаем файл
    if not path.exists():
        data = _empty_store()
    else:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or "users" not in raw:
                data = _empty_store()
            else:
                data = raw
        except Exception:
            data = _empty_store()
    _cache = data
    _cache_path = path
    return _cache


def _save_raw(path: Path, data: dict[str, Any]) -> None:
    global _cache, _cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Атомарная запись с ограниченными правами (0o600 — только владелец)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        offset = 0
        total = len(encoded)
        while offset < total:
            written = os.write(fd, encoded[offset:])
            offset += written
    finally:
        os.close(fd)
    tmp.replace(path)
    # Обновляем кэш после успешной записи
    _cache = data
    _cache_path = path


def _prefs_from_dict(d: dict[str, Any]) -> UserPrefs:
    """Создать UserPrefs из dict. Если d не dict — вернуть дефолт."""
    if not isinstance(d, dict):
        _log.warning("_prefs_from_dict: expected dict, got %s — using defaults", type(d).__name__)
        return UserPrefs()
    return UserPrefs(
        watchlist=list(d.get("watchlist") or []),
        notify_strong_outside=bool(d.get("notify_strong_outside", True)),
        strong_threshold=float(d.get("strong_threshold", 0.35)),
        notify_cooldown_sec=int(d.get("notify_cooldown_sec", 86_400)),
        last_notify_ts={str(k): float(v) for k, v in (d.get("last_notify_ts") or {}).items()},
        autocollect_tickers=list(d.get("autocollect_tickers") or []),
        use_default_tickers=bool(d.get("use_default_tickers", True)),
        tier=str(d.get("tier", "free")),
        receive_learning_report=bool(d.get("receive_learning_report", False)),
        signal_filter_type=str(d.get("signal_filter_type", "balanced")),
        language=str(d.get("language", "ru")),
        auto_collect=bool(d.get("auto_collect", False)),
        max_watchlist_size=int(d.get("max_watchlist_size", 30)),
        notify_drawdown=bool(d.get("notify_drawdown", True)),
        daily_digest=bool(d.get("daily_digest", False)),
    )


def load_prefs(user_id: int, path: Path | None = None) -> UserPrefs:
    p = path or _default_data_path()
    with _lock:
        raw = _load_raw(p)
        u = raw["users"].get(str(user_id))
        if not u:
            return UserPrefs()
        return _prefs_from_dict(u)


def save_prefs(user_id: int, prefs: UserPrefs, path: Path | None = None) -> None:
    p = path or _default_data_path()
    with _lock:
        raw = _load_raw(p)
        raw["users"][str(user_id)] = asdict(prefs)
        _save_raw(p, raw)


def normalize_symbol(sym: str) -> str:
    """Нормализовать тикер: trim, убрать внутренние пробелы, upper.
    НЕ удаляем точки — они нужны для .ME, BRK.B и т.д.
    Конвертация BRK.B → BRK-B делается в _symbol_for_yahoo (market_data.py).
    """
    return "".join(sym.split()).upper()


def all_user_ids(path: Path | None = None) -> list[int]:
    p = path or _default_data_path()
    with _lock:
        raw = _load_raw(p)
    out: list[int] = []
    for k in raw.get("users", {}):
        try:
            out.append(int(k))
        except ValueError:
            continue
    return out


def can_notify_again(prefs: UserPrefs, symbol: str) -> bool:
    sym = normalize_symbol(symbol)
    ts = prefs.last_notify_ts.get(sym)
    # cooldown=0 означает force-refresh — всегда разрешить
    if prefs.notify_cooldown_sec == 0:
        return True
    if ts is None:
        return True
    # Календарный день: одно уведомление на тикер в сутки (UTC)
    last_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    return last_date < today


def mark_notified(prefs: UserPrefs, symbol: str) -> None:
    prefs.last_notify_ts[normalize_symbol(symbol)] = time.time()
