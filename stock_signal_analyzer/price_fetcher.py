"""
Unified price fetcher с приоритетом источников.

Приоритет:
- .ME (российские) → Т-Банк (приоритет), fallback yfinance
- US → Polygon (если ключ) → Finnhub (если ключ) → yfinance

История (OHLCV):
- .ME → Т-Банк → yfinance
- US → Polygon → yfinance

Текущая цена:
- .ME → Т-Банк → yfinance
- US → Finnhub → Polygon → yfinance
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

_log = logging.getLogger(__name__)

# ── Retry helper ─────────────────────────────────────────────────────────────


def _retry_fetch(func, max_retries: int = 3, backoff: float = 2.0):
    """Выполнить func с exponential backoff."""
    last_err = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "too many requests" in msg or "rate limited" in msg:
                wait = backoff * (2 ** attempt)
                _log.warning("Rate limit, retry in %.1fs (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise
    raise last_err


# ── T-Bank helpers ───────────────────────────────────────────────────────────


def _is_ru(symbol: str) -> bool:
    return symbol.strip().upper().endswith(".ME")


def _tk_history(symbol: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """История через Т-Банк (для .ME тикеров)."""
    try:
        from .tbank_invest import fetch_daily_history
        # fetch_daily_history принимает days, не date range
        days = (datetime.now(timezone.utc) - start).days + 5
        df = fetch_daily_history(symbol, days=days)
        if df is None or df.empty:
            return None
        # Фильтруем по диапазону дат
        if hasattr(df.index, 'tz') and df.index.tz is None:
            df.index = df.index.tz_localize(timezone.utc)
        mask = (df.index >= start) & (df.index <= end)
        return df.loc[mask] if mask.any() else None
    except Exception as e:
        _log.debug("T-Bank history failed for %s: %s", symbol, e)
        return None


def _tk_current(symbol: str) -> float | None:
    """Текущая цена через Т-Банк (для .ME тикеров)."""
    try:
        from .tbank_invest import fetch_last_price_tbank
        q = fetch_last_price_tbank(symbol)
        if q and q.last_price and q.last_price > 0:
            return float(q.last_price)
    except Exception as e:
        _log.debug("T-Bank current price failed for %s: %s", symbol, e)
    return None


# ── Polygon helpers ──────────────────────────────────────────────────────────


def _pg_history(symbol: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """История через Polygon (только US, без .ME)."""
    if _is_ru(symbol):
        return None
    try:
        from .polygon_data import _api_key, _request_with_fallback
        key = _api_key()
        if not key:
            return None
        sym = symbol.strip().upper()
        # Polygon /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}
        from_str = start.strftime("%Y-%m-%d")
        to_str = end.strftime("%Y-%m-%d")
        r = _request_with_fallback(
            method="GET",
            path_v2=f"/aggs/ticker/{sym}/range/1/day/{from_str}/{to_str}",
            api_key=key,
            params={"adjusted": "true", "sort": "asc", "limit": 50000},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        rows = []
        for bar in results:
            ts = bar.get("t")
            if ts:
                dt = pd.Timestamp(ts, unit="ms", tz="UTC")
            else:
                continue
            rows.append({
                "Open": float(bar.get("o", 0)),
                "High": float(bar.get("h", 0)),
                "Low": float(bar.get("l", 0)),
                "Close": float(bar.get("c", 0)),
                "Volume": float(bar.get("v", 0)),
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.index = pd.DatetimeIndex([pd.Timestamp(r["t"], unit="ms", tz="UTC") for r in results if r.get("t")])
        return df
    except Exception as e:
        _log.debug("Polygon history failed for %s: %s", symbol, e)
        return None


def _pg_current(symbol: str) -> float | None:
    """Текущая цена через Polygon."""
    if _is_ru(symbol):
        return None
    try:
        from .polygon_data import fetch_prev_close
        q = fetch_prev_close(symbol)
        if q and q.last_price and q.last_price > 0:
            return float(q.last_price)
    except Exception as e:
        _log.debug("Polygon current price failed for %s: %s", symbol, e)
    return None


# ── Finnhub helpers ──────────────────────────────────────────────────────────


def _fh_current(symbol: str) -> float | None:
    """Текущая цена через Finnhub (US)."""
    if _is_ru(symbol):
        return None
    try:
        from .finnhub_live import fetch_quote
        q = fetch_quote(symbol)
        if q and q.current and q.current > 0:
            return float(q.current)
    except Exception as e:
        _log.debug("Finnhub current price failed for %s: %s", symbol, e)
    return None


# ── yfinance fallback ────────────────────────────────────────────────────────


def _yf_history(symbol: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """История через yfinance (fallback)."""
    try:
        def _fetch():
            ticker = yf.Ticker(symbol)
            return ticker.history(start=start, end=end, interval="1d")

        hist = _retry_fetch(_fetch)
        if hist is None or hist.empty:
            return None
        return hist
    except Exception as e:
        _log.debug("yfinance history failed for %s: %s", symbol, e)
        return None


def _yf_current(symbol: str) -> float | None:
    """Текущая цена через yfinance (fallback)."""
    try:
        def _fetch():
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="5d", interval="1d")
            if data.empty:
                return None
            return float(data["Close"].iloc[-1])

        return _retry_fetch(_fetch)
    except Exception as e:
        _log.debug("yfinance current price failed for %s: %s", symbol, e)
        return None


# ── Публичный API ────────────────────────────────────────────────────────────


def fetch_history(symbol: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """
    Загрузить исторические OHLCV данные.
    Приоритет:
      .ME → Т-Банк → yfinance
      US  → Polygon → yfinance
    """
    symbol = symbol.strip().upper()
    is_ru = _is_ru(symbol)

    # 1. Т-Банк для .ME
    if is_ru:
        df = _tk_history(symbol, start, end)
        if df is not None and not df.empty:
            _log.debug("History for %s: T-Bank (%d bars)", symbol, len(df))
            return df

    # 2. Polygon для US
    if not is_ru:
        df = _pg_history(symbol, start, end)
        if df is not None and not df.empty:
            _log.debug("History for %s: Polygon (%d bars)", symbol, len(df))
            return df

    # 3. yfinance fallback
    df = _yf_history(symbol, start, end)
    if df is not None and not df.empty:
        _log.debug("History for %s: yfinance (%d bars)", symbol, len(df))
        return df

    _log.warning("No history data for %s", symbol)
    return None


def fetch_current_price(symbol: str) -> float | None:
    """
    Получить текущую/последнюю цену.
    Приоритет:
      .ME → Т-Банк → yfinance
      US  → Finnhub → Polygon → yfinance
    """
    symbol = symbol.strip().upper()
    is_ru = _is_ru(symbol)

    # 1. Т-Банк для .ME (и US тоже если доступен)
    price = _tk_current(symbol)
    if price is not None:
        _log.debug("Current price for %s: T-Bank = %.4f", symbol, price)
        return price

    # 2. Finnhub для US
    if not is_ru:
        price = _fh_current(symbol)
        if price is not None:
            _log.debug("Current price for %s: Finnhub = %.4f", symbol, price)
            return price

    # 3. Polygon для US
    if not is_ru:
        price = _pg_current(symbol)
        if price is not None:
            _log.debug("Current price for %s: Polygon = %.4f", symbol, price)
            return price

    # 4. yfinance fallback
    price = _yf_current(symbol)
    if price is not None:
        _log.debug("Current price for %s: yfinance = %.4f", symbol, price)
        return price

    _log.warning("No current price for %s", symbol)
    return None


# ── Для outcome tracker ─────────────────────────────────────────────────────


def fetch_price_for_outcome(symbol: str, date: datetime | None = None) -> float | None:
    """
    Получить цену для outcome tracker.
    Если date передана — ищем цену на эту дату в истории.
    Если date None — текущая цена.
    """
    if date is None:
        return fetch_current_price(symbol)

    # Загружаем историю с запасом
    end = datetime.now(timezone.utc)
    start = date - timedelta(days=5)
    hist = fetch_history(symbol, start, end)
    if hist is None or hist.empty:
        return None

    # Ищем ближайшую дату >= date
    if hasattr(hist.index, 'tz') and hist.index.tz is None:
        hist.index = hist.index.tz_localize(timezone.utc)
    mask = hist.index >= date
    if mask.any():
        return float(hist.loc[mask, "Close"].iloc[0])

    # Fallback — последняя доступная цена
    return float(hist["Close"].iloc[-1])
