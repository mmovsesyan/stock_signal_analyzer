"""Настройки пользователя Telegram: список бумаг, уведомления о сильных сигналах вне списка."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


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
        os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    tmp.replace(path)
    # Обновляем кэш после успешной записи
    _cache = data
    _cache_path = path


def _prefs_from_dict(d: dict[str, Any]) -> UserPrefs:
    return UserPrefs(
        watchlist=list(d.get("watchlist") or []),
        notify_strong_outside=bool(d.get("notify_strong_outside", True)),
        strong_threshold=float(d.get("strong_threshold", 0.35)),
        notify_cooldown_sec=int(d.get("notify_cooldown_sec", 86_400)),
        last_notify_ts={str(k): float(v) for k, v in (d.get("last_notify_ts") or {}).items()},
        autocollect_tickers=list(d.get("autocollect_tickers") or []),
        use_default_tickers=bool(d.get("use_default_tickers", True)),
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
    return sym.strip().upper().replace(" ", "")


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
    if ts is None:
        return True
    return (time.time() - ts) >= prefs.notify_cooldown_sec


def mark_notified(prefs: UserPrefs, symbol: str) -> None:
    prefs.last_notify_ts[normalize_symbol(symbol)] = time.time()
